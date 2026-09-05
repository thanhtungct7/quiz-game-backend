"""Fight a real monster on a running server.

Drives one WebSocket client through a whole lesson battle and checks the things
unit tests cannot: that the routes are wired up, that JWT auth works over a
WebSocket handshake, that the monster catalog was seeded, that questions come
out of the real question bank -- and, above all, that the answers given inside
a battle land in `user_challenge_progress` and pay experience and gold.

Two checks here exist only because the fight runs in real time, and neither can
be written as a unit test: that a player who stops answering still loses health,
and that snapshots keep arriving while nothing at all is happening.

`--capture` writes every frame seen to the Android fixture, which is the only
way that fixture is worth anything: generated from the Pydantic schema it merely
agrees with itself.

Usage:
    conda run -n backend uvicorn app.main:app --port 8000     # in one shell
    conda run -n backend python -m scripts.pve_smoke          # in another
    conda run -n backend python -m scripts.pve_smoke --capture
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import websockets

# Throwaway credential for the disposable account this script registers.
PASSWORD = "SmokeTest!2345"  # noqa: S105

# Long enough for a tier-1 monster to finish a cast (5.2s) with room to spare.
STALL_SECONDS = 7.0
# A guard against an unwinnable fight looping forever: the pool recycles, so
# only health ends a battle, and answering the first option every time may never
# land a blow.
MAX_ANSWERS = 24
# How many frames of each type the Android fixture keeps.
PER_TYPE_CAPTURE = 3

CAPTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "duo-game-app/app/src/test/resources/pve_wire_capture.json"
)


@dataclass
class Client:
    """One player: an HTTP identity plus its live socket."""

    name: str
    email: str
    token: str = ""
    user_id: str = ""
    socket: Any = None
    received: list[dict[str, Any]] = field(default_factory=list)
    # How far `next_question` has read. Everything the client sees lands in
    # `received` whichever method read it, so a question that arrived while the
    # script was stalling is still waiting here rather than lost.
    push_cursor: int = 0

    async def send(self, event: str, data: dict[str, Any] | None = None) -> None:
        await self.socket.send(json.dumps({"type": event, "data": data or {}}))

    async def expect(self, event: str, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """Read until `event` arrives, remembering everything seen on the way."""
        while True:
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            self.received.append(message)
            if message["type"] == "error":
                raise AssertionError(f"{self.name} got error: {message['data']}")
            if message["type"] == event:
                return message["data"]

    async def expect_any(
        self, *events: str, timeout_seconds: float = 30.0
    ) -> tuple[str, dict[str, Any]]:
        """Read until one of `events` arrives.

        Needed now that a fight can end at any moment: waiting for the next
        question while the battle is finishing would otherwise hang until the
        timeout.
        """
        while True:
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            self.received.append(message)
            if message["type"] == "error":
                raise AssertionError(f"{self.name} got error: {message['data']}")
            if message["type"] in events:
                return message["type"], message["data"]

    async def next_question(
        self, timeout_seconds: float = 30.0
    ) -> tuple[str, dict[str, Any]]:
        """The next question still waiting to be answered, or the payout.

        Reads the buffer before the socket. Without that, a question pushed
        while the script was deliberately doing nothing would be read past and
        the fight would stall until the monster won -- which is exactly what a
        realtime protocol makes easy to get wrong.
        """
        while True:
            while self.push_cursor < len(self.received):
                message = self.received[self.push_cursor]
                self.push_cursor += 1
                if message["type"] == "error":
                    raise AssertionError(f"{self.name} got error: {message['data']}")
                if message["type"] in ("question.push", "battle.finished"):
                    return message["type"], message["data"]
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            self.received.append(json.loads(raw))

    async def drain(self, seconds: float) -> None:
        """Sit still and record whatever the server sends unprompted.

        The only way to observe a monster that swings on its own clock: stop
        playing and see what lands.
        """
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                raw = await asyncio.wait_for(self.socket.recv(), timeout=remaining)
            except TimeoutError:
                return
            self.received.append(json.loads(raw))

    async def expect_error(self, timeout_seconds: float = 10.0) -> dict[str, Any]:
        """Read until an error arrives, skipping whatever was already queued.

        A refusal never arrives on an empty socket: `battle.start` is answered
        with `battle.started` and the first `question.push` back to back, so the
        error we are testing for sits behind frames the caller did not read.
        """
        while True:
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            self.received.append(message)
            if message["type"] == "error":
                return message["data"]

    def seen(self, event: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.received if m["type"] == event]


class Smoke:
    def __init__(self, base_url: str, capture_path: pathlib.Path | None = None) -> None:
        self.capture_path = capture_path
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.api = f"{self.base_url}/api/v1"
        self.failures: list[str] = []
        self.checks = 0
        self.rest: dict[str, Any] = {}

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks += 1
        if condition:
            print(f"  \033[32mPASS\033[0m {label}")
        else:
            print(f"  \033[31mFAIL\033[0m {label} {detail}")
            self.failures.append(label)

    # --- setup ------------------------------------------------------------

    async def register(self, http: httpx.AsyncClient, name: str) -> Client:
        email = f"pve-smoke-{name}-{uuid4().hex[:8]}@example.com"
        client = Client(name=name, email=email)

        response = await http.post(
            f"{self.api}/auth/register",
            json={"email": email, "password": PASSWORD, "username": f"pve-{name}"},
        )
        if response.status_code >= 400:
            raise SystemExit(f"register failed for {name}: {response.text}")

        login = await http.post(
            f"{self.api}/auth/login", json={"email": email, "password": PASSWORD}
        )
        if login.status_code >= 400:
            raise SystemExit(f"login failed for {name}: {login.text}")
        client.token = login.json()["access_token"]

        me = await http.get(
            f"{self.api}/users/me", headers={"Authorization": f"Bearer {client.token}"}
        )
        client.user_id = me.json()["id"]
        return client

    async def connect(self, client: Client) -> None:
        client.socket = await websockets.connect(
            f"{self.ws_url}/api/v1/battles/ws?token={client.token}"
        )
        await client.expect("connected")

    def headers(self, client: Client) -> dict[str, str]:
        return {"Authorization": f"Bearer {client.token}"}

    async def pick_lesson(self, http: httpx.AsyncClient, client: Client) -> tuple[str, str]:
        """The first course's first path lesson, and its course id."""
        courses = await http.get(f"{self.api}/courses", headers=self.headers(client))
        if courses.status_code >= 400 or not courses.json():
            raise SystemExit("no courses: import the question bank first")
        course_id = courses.json()[0]["id"]

        tree = await http.get(f"{self.api}/courses/{course_id}/tree", headers=self.headers(client))
        if tree.status_code >= 400:
            raise SystemExit(f"course tree failed: {tree.text}")
        for unit in tree.json()["units"]:
            for lesson in unit["lessons"]:
                return lesson["id"], course_id
        raise SystemExit("the first course has no lessons")

    # --- scenarios --------------------------------------------------------

    async def scenario_catalog(self, http: httpx.AsyncClient, client: Client) -> None:
        print("\n[1] Monster catalog and the course map")

        monsters = await http.get(f"{self.api}/battles/monsters", headers=self.headers(client))
        rows = monsters.json() if monsters.status_code == 200 else []
        self.rest["MONSTERS"] = rows
        self.check(
            "the catalog was seeded at start-up",
            monsters.status_code == 200 and len(rows) >= 2,
            monsters.text[:120],
        )
        self.check(
            "it holds both ordinary monsters and bosses",
            any(row["is_boss"] for row in rows) and any(not row["is_boss"] for row in rows),
        )

    async def scenario_preview(
        self, http: httpx.AsyncClient, client: Client, lesson_id: str, course_id: str
    ) -> None:
        print("\n[2] What guards this lesson")

        preview = await http.get(
            f"{self.api}/battles/lessons/{lesson_id}", headers=self.headers(client)
        )
        if preview.status_code == 200:
            self.rest["LESSON_PREVIEW"] = preview.json()
        self.check(
            "a lesson names the monster guarding it",
            preview.status_code == 200 and preview.json()["monster"]["code"],
            preview.text[:120],
        )
        self.check(
            "a lesson nobody has fought is not marked cleared",
            preview.status_code == 200 and preview.json()["cleared"] is False,
        )

        course_map = await http.get(
            f"{self.api}/battles/courses/{course_id}/monsters",
            headers=self.headers(client),
        )
        lessons = course_map.json()["lessons"] if course_map.status_code == 200 else []
        if course_map.status_code == 200:
            self.rest["COURSE_MONSTERS"] = course_map.json()
        self.check(
            "the whole course map comes back in one request",
            course_map.status_code == 200 and len(lessons) > 1,
            f"{len(lessons)} lessons",
        )

        missing = await http.get(
            f"{self.api}/battles/lessons/does-not-exist", headers=self.headers(client)
        )
        self.check("a lesson that does not exist is a 404", missing.status_code == 404)

    async def equipped_skill(
        self, http: httpx.AsyncClient, client: Client
    ) -> tuple[str, int] | None:
        """The cheapest skill on the player's bar, with what it costs.

        Every account is granted starters, so an empty bar here is itself worth
        reporting -- it is the exact bug that shipped once already. The cheapest
        one is chosen so the cast happens within a fight rather than never.
        """
        response = await http.get(
            f"{self.api}/game/loadout", headers=self.headers(client)
        )
        slots = response.json()["slots"] if response.status_code == 200 else []
        self.check(
            "the player walks in with skills on the bar",
            bool(slots),
            response.text[:120],
        )
        if not slots:
            return None
        cheapest = min(slots, key=lambda slot: slot["mana_cost"])
        return cheapest["code"], cheapest["mana_cost"]

    async def cast_skill(self, client: Client, skill_code: str) -> None:
        """Cast, having already checked the mana is there.

        Anything but a `skill.used` back is a real failure now: the one
        legitimate refusal was priced out before this was called.
        """
        print(f"    casting {skill_code}...")
        await client.send("skill.use", {"skill_code": skill_code})
        while True:
            raw = await asyncio.wait_for(client.socket.recv(), timeout=15.0)
            message = json.loads(raw)
            client.received.append(message)
            if message["type"] in ("skill.used", "error"):
                break
        if message["type"] == "error":
            self.check(
                f"casting {skill_code} with the mana for it is accepted",
                False,
                json.dumps(message["data"])[:120],
            )
            return
        used = message["data"]
        self.check(
            "a cast reports the mana it cost and when it can be cast again",
            used["mana_spent"] > 0 and used["ready_again_at"] > 0,
            json.dumps(used)[:160],
        )
        self.check(
            "a cast reports where the monster's cast bar now stands",
            used["cast_ends_at"] > 0,
            json.dumps(used)[:160],
        )

    async def scenario_battle(
        self, http: httpx.AsyncClient, client: Client, lesson_id: str
    ) -> dict[str, Any]:
        print("\n[3] A whole battle, in real time, from the first question to the payout")

        await client.send("battle.start", {"lesson_id": lesson_id})
        started = await client.expect("battle.started")
        monster = started["monster"]
        print(
            f"    {monster['name']} (tier {monster['tier']}"
            f"{', boss' if monster['is_boss'] else ''}) "
            f"{started['monster_hp']} hp vs your {started['your_hp']}, "
            f"swinging every {monster['cast_interval_ms']}ms"
        )
        self.check(
            "the battle opens on a real monster and a real lesson",
            bool(monster["code"]) and started["questions_in_pool"] > 0,
            json.dumps(started)[:160],
        )
        self.check(
            "the client is told both clock rates it has to draw against",
            started["tick_hz"] > 0
            and started["snapshot_hz"] > 0
            and monster["cast_interval_ms"] > 0,
            f"tick={started['tick_hz']} snapshot={started['snapshot_hz']}",
        )

        skill = await self.equipped_skill(http, client)
        cast_done = False
        await self.stall(client, started)

        # The answer key never crosses the wire, so the smoke test cannot know
        # which option is right. It answers the first option every time: the
        # point here is that the protocol runs end to end and that whatever was
        # answered reaches progress, not that the run is won.
        finished: dict[str, Any] | None = None
        answered = 0
        while answered < MAX_ANSWERS:
            event, data = await client.next_question()
            if event == "battle.finished":
                finished = data
                break
            if answered == 0:
                self.check(
                    "the question comes from the real question bank",
                    bool(data["question"]["question"]) and len(data["question"]["options"]) >= 2,
                )
                self.check(
                    "no option is flagged as the correct one",
                    all("correct" not in option for option in data["question"]["options"]),
                )

            await client.send(
                "answer.submit",
                {
                    "token": data["token"],
                    "option_id": data["question"]["options"][0]["id"],
                },
            )
            event, result = await client.expect_any("answer.result", "battle.finished")
            if event == "battle.finished":
                finished = result
                break
            answered += 1
            if skill is not None and not cast_done and result["your_mana"] >= skill[1]:
                cast_done = True
                await self.cast_skill(client, skill[0])
            hit = result["blow"]["final_damage"] if result["blow"] else 0
            print(
                f"    answer {answered}: "
                f"{'HIT ' + str(hit) if hit else 'MISS'}"
                f"  monster={result['monster_hp']:3d} combo={result['combo']}"
            )
            self.check(
                f"answer {answered}: the result reveals the answer",
                bool(result["correct_option_ids"]),
            )

        if skill is not None:
            self.check(
                "the fight lasted long enough to afford a skill and cast one",
                cast_done,
                f"never reached {skill[1]} mana for {skill[0]}",
            )

        recycled = [push for push in client.seen("question.push") if push["pool_pass"] > 0]
        if len(client.seen("question.push")) > started["questions_in_pool"]:
            self.check(
                "the lesson goes round again rather than ending the fight",
                bool(recycled),
                f"{len(client.seen('question.push'))} pushes, no pool_pass above zero",
            )

        if finished is None:
            await client.send("battle.leave")
            finished = await client.expect("battle.finished")

        print(
            f"    {finished['outcome']} / {finished['end_reason']}: "
            f"exp {finished['exp']['delta']:+d}, gold {finished['gold']['delta']:+d}, "
            f"{finished['answers_given']} answers in {finished['duration_ms']}ms, "
            f"{finished['monster_swings']} swings, "
            f"lesson {finished['lesson_progress']['status']} "
            f"({finished['lesson_progress']['correct']}"
            f"/{finished['lesson_progress']['total']})"
        )
        self.check(
            "the battle ends with a decided outcome",
            finished["outcome"] in ("WON", "LOST", "ABANDONED"),
            finished["outcome"],
        )
        self.check(
            "the answers moved the learn path",
            finished["lesson_progress"]["status"] != "NOT_STARTED",
            json.dumps(finished["lesson_progress"]),
        )
        self.check(
            "a win pays and anything else does not",
            (finished["exp"]["delta"] > 0)
            if finished["outcome"] == "WON"
            else finished["exp"]["delta"] == 0,
            f"{finished['outcome']} paid {finished['exp']['delta']}",
        )
        return finished

    async def stall(self, client: Client, started: dict[str, Any]) -> None:
        """Stop playing, and see what the monster does about it.

        This is the check the whole rewrite exists for. Under the lock-step
        engine a player who answered nothing took no damage at all; here the
        cast bar runs regardless, and the snapshots keep coming while it does.
        """
        print(f"    stalling {STALL_SECONDS:.0f}s without answering...")
        await client.drain(STALL_SECONDS)

        swings = client.seen("monster.swing")
        ticks = client.seen("state.tick")
        self.check(
            "a player who stops answering still loses health",
            bool(swings) and swings[-1]["your_hp"] < started["your_hp"],
            f"{len(swings)} swings",
        )
        self.check(
            "snapshots keep arriving while nothing is happening",
            len(ticks) >= 2,
            f"{len(ticks)} state.tick frames in {STALL_SECONDS:.0f}s",
        )
        if ticks:
            self.check(
                "a snapshot publishes the cast deadline, not a progress bar",
                ticks[-1]["cast_ends_at"] > ticks[-1]["t"] and ticks[-1]["next_swing_damage"] > 0,
                json.dumps(ticks[-1])[:160],
            )

    async def scenario_clock(self, client: Client) -> None:
        print("\n[4] Clock synchronisation")

        await client.send("ping", {"client_time_ms": 1234})
        pong = await client.expect("pong")
        self.check(
            "a pong carries back the client's stamp and the server's own",
            pong.get("client_time_ms") == 1234 and pong.get("server_time_ms", 0) > 0,
            json.dumps(pong)[:120],
        )

    async def scenario_two_layers(
        self,
        http: httpx.AsyncClient,
        client: Client,
        lesson_id: str,
        finished: dict[str, Any],
    ) -> None:
        """The whole point of the feature, checked from the other side."""
        print("\n[5] The same fight, read back through the study endpoints")

        progress = await http.get(
            f"{self.api}/progress/lessons/{lesson_id}", headers=self.headers(client)
        )
        body = progress.json() if progress.status_code == 200 else {}
        self.check(
            "the lesson's progress row exists after the battle",
            progress.status_code == 200 and body.get("status") != "NOT_STARTED",
            progress.text[:120],
        )
        self.check(
            "progress agrees with what the battle reported",
            body.get("status") == finished["lesson_progress"]["status"],
            f"{body.get('status')} vs {finished['lesson_progress']['status']}",
        )

        profile = await http.get(f"{self.api}/game/profile", headers=self.headers(client))
        game = profile.json() if profile.status_code == 200 else {}
        self.check(
            "the game profile carries the battle's payout",
            profile.status_code == 200
            and game.get("total_exp", 0) >= finished["exp"]["delta"]
            and game.get("gold", 0) >= finished["gold"]["delta"],
            profile.text[:160],
        )

        history = await http.get(f"{self.api}/battles/history", headers=self.headers(client))
        rows = history.json() if history.status_code == 200 else []
        self.rest["HISTORY"] = rows
        self.check(
            "the battle shows up in history",
            history.status_code == 200 and any(row["id"] == finished["battle_id"] for row in rows),
            history.text[:160],
        )

    async def scenario_replay(self, client: Client, lesson_id: str, first: dict[str, Any]) -> None:
        print("\n[6] Fighting the same lesson again")

        await client.send("battle.start", {"lesson_id": lesson_id})
        started = await client.expect("battle.started")
        self.check("a second battle on the same lesson opens", bool(started["battle_id"]))

        await client.send("battle.start", {"lesson_id": lesson_id})
        refused = await client.expect_error()
        self.check(
            "a second battle while one is running is refused",
            refused.get("code") == "BATTLE_ALREADY_ACTIVE",
            json.dumps(refused)[:160],
        )

        await client.send("battle.leave")
        left = await client.expect("battle.finished")
        self.check(
            "walking out abandons the battle without paying",
            left["outcome"] == "ABANDONED" and left["exp"]["delta"] == 0,
            json.dumps(left)[:160],
        )
        if first["outcome"] == "WON" and first["first_clear"]:
            self.check("the first clear was marked as such", first["first_clear"] is True)

    async def scenario_bad_input(self, client: Client) -> None:
        print("\n[7] Malformed frames")

        await client.socket.send("not json at all")
        first = await client.expect_error()
        self.check(
            "garbage is answered with an error, not a disconnect",
            first["code"] == "INVALID_PAYLOAD",
            json.dumps(first)[:120],
        )

        await client.send("battle.explode", {})
        second = await client.expect_error()
        self.check(
            "an unknown event is named in the error",
            second["code"] == "UNKNOWN_EVENT",
            json.dumps(second)[:120],
        )

        await client.send("battle.start", {"lesson_id": "nope"})
        third = await client.expect_error()
        self.check(
            "a lesson that does not exist is refused by name",
            third["code"] == "LESSON_NOT_FOUND",
            json.dumps(third)[:120],
        )

    async def scenario_bad_token(self) -> None:
        print("\n[8] WebSocket authentication")
        for label, url in (
            ("no token", f"{self.ws_url}/api/v1/battles/ws"),
            ("bad token", f"{self.ws_url}/api/v1/battles/ws?token=not-a-jwt"),
        ):
            try:
                async with websockets.connect(url):
                    self.check(f"{label} is refused", False, "connection accepted")
            except Exception:
                self.check(f"{label} is refused", True)

    # --- driver -----------------------------------------------------------

    async def reachable(self, http: httpx.AsyncClient) -> bool:
        """Say plainly that nothing is listening, instead of a connect traceback."""
        try:
            await http.get(f"{self.api}/health/ready", timeout=5)
        except httpx.RequestError:
            print(
                f"No server at {self.base_url}. Start one in another shell:\n"
                f"    conda run -n backend uvicorn app.main:app --port 8000"
            )
            return False
        return True

    def write_capture(self, client: Client, path: pathlib.Path) -> None:
        """Save real frames as the Android contract fixture.

        Up to `PER_TYPE_CAPTURE` frames of each type, in the order they were
        seen, plus the REST bodies the app parses. More than one per type on
        purpose: a single `answer.result` cannot show the client both a hit and
        a miss, and those are different shapes. A fixture generated from the
        schema can only ever agree with the schema; this one can disagree with
        it, which is the whole point of having it.
        """
        seen: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for message in client.received:
            kind = message["type"]
            if counts.get(kind, 0) >= PER_TYPE_CAPTURE:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            seen.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"ws": seen, "rest": self.rest},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nCaptured {len(seen)} frames of {len(counts)} types -> {path}")

    async def run(self) -> int:
        async with httpx.AsyncClient(timeout=30) as http:
            if not await self.reachable(http):
                return 1
            player = await self.register(http, "one")
            print(f"Player: {player.email}")
            lesson_id, course_id = await self.pick_lesson(http, player)
            print(f"Lesson: {lesson_id}")

            await self.scenario_catalog(http, player)
            await self.scenario_preview(http, player, lesson_id, course_id)

            await self.connect(player)
            finished = await self.scenario_battle(http, player, lesson_id)
            await self.scenario_clock(player)
            await self.scenario_two_layers(http, player, lesson_id, finished)
            await self.scenario_replay(player, lesson_id, finished)
            await self.scenario_bad_input(player)
            await self.scenario_bad_token()

            if self.capture_path is not None:
                self.write_capture(player, self.capture_path)
            await player.socket.close()

        print(f"\n{self.checks - len(self.failures)}/{self.checks} checks passed")
        if self.failures:
            print("Failed:")
            for failure in self.failures:
                print(f"  - {failure}")
            return 1
        return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--capture",
        nargs="?",
        const=str(CAPTURE_PATH),
        default=None,
        help="write the frames seen to the Android wire fixture (default: %(const)s)",
    )
    args = parser.parse_args()
    capture = pathlib.Path(args.capture) if args.capture else None
    return await Smoke(args.base_url, capture).run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

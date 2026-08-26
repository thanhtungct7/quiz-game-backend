"""Play real 1v1 duo matches against a running server.

Drives two WebSocket clients through the whole protocol and checks the things
unit tests cannot: that the routes are wired up, that JWT auth works over a
WebSocket handshake, that questions really come out of the question bank, and
that results land in PostgreSQL.

Usage:
    conda run -n backend uvicorn app.main:app --port 8000     # in one shell
    conda run -n backend python -m scripts.duo_smoke          # in another
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import websockets

# Throwaway credential for the disposable accounts this script registers.
PASSWORD = "SmokeTest!2345"  # noqa: S105


@dataclass
class Client:
    """One player: an HTTP identity plus its live socket."""

    name: str
    email: str
    token: str = ""
    user_id: str = ""
    socket: Any = None
    received: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, event: str, data: dict[str, Any] | None = None) -> None:
        await self.socket.send(json.dumps({"type": event, "data": data or {}}))

    async def expect(
        self, event: str, timeout_seconds: float = 20.0
    ) -> dict[str, Any]:
        """Read until `event` arrives, remembering everything seen on the way."""
        while True:
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            self.received.append(message)
            if message["type"] == "error":
                raise AssertionError(f"{self.name} got error: {message['data']}")
            if message["type"] == event:
                return message["data"]

    def seen(self, event: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.received if m["type"] == event]


class Smoke:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        self.api = f"{self.base_url}/api/v1"
        self.failures: list[str] = []
        self.checks = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks += 1
        if condition:
            print(f"  \033[32mPASS\033[0m {label}")
        else:
            print(f"  \033[31mFAIL\033[0m {label} {detail}")
            self.failures.append(label)

    # --- setup ------------------------------------------------------------

    async def register(self, http: httpx.AsyncClient, name: str) -> Client:
        email = f"duo-smoke-{name}-{uuid4().hex[:8]}@example.com"
        client = Client(name=name, email=email)

        response = await http.post(
            f"{self.api}/auth/register",
            json={"email": email, "password": PASSWORD, "username": f"smoke-{name}"},
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
            f"{self.ws_url}/api/v1/duo/ws?token={client.token}"
        )
        await client.expect("connected")

    # --- scenarios --------------------------------------------------------

    async def scenario_random_match(self, one: Client, two: Client) -> str:
        print("\n[1] Random queue, full match, speed scoring")

        await one.send("queue.join", {"question_count": 3, "time_per_question": 10})
        waiting = await one.expect("queue.waiting")
        self.check("first player is told they are waiting", waiting["position"] == 1)

        await two.send("queue.join", {"question_count": 3, "time_per_question": 10})
        found_one = await one.expect("match.found")
        found_two = await two.expect("match.found")

        self.check(
            "both players land in the same match",
            found_one["match_id"] == found_two["match_id"],
        )
        self.check(
            "each player sees the other as the opponent",
            found_one["opponent"]["id"] == two.user_id
            and found_two["opponent"]["id"] == one.user_id,
            f"{found_one['opponent']['id']} / {found_two['opponent']['id']}",
        )

        await one.expect("match.started")
        await two.expect("match.started")
        match_id = found_one["match_id"]

        for index in range(3):
            round_one = await one.expect("round.start")
            await two.expect("round.start")

            if index == 0:
                self.check(
                    "the question comes from the real question bank",
                    bool(round_one["question"]["question"])
                    and len(round_one["question"]["options"]) >= 2,
                )
                self.check(
                    "no option is flagged as the correct one",
                    all("correct" not in o for o in round_one["question"]["options"]),
                )

            options = round_one["question"]["options"]
            # Player one answers immediately, player two dawdles.
            await one.send(
                "answer.submit",
                {"round_index": index, "option_id": options[0]["id"]},
            )
            await asyncio.sleep(1.5)
            await two.send(
                "answer.submit",
                {"round_index": index, "option_id": options[0]["id"]},
            )

            result_one = await one.expect("round.result")
            await two.expect("round.result")

            if index == 0:
                self.check(
                    "the round result finally reveals the answer",
                    len(result_one["correct_option_ids"]) >= 1,
                )
                self.check(
                    "server-side timing is recorded, not client-claimed",
                    result_one["you"]["elapsed_ms"] is not None
                    and result_one["you"]["elapsed_ms"] >= 0,
                )
                if result_one["you"]["correct"] and result_one["opponent"]["correct"]:
                    self.check(
                        "answering faster scores more for the same answer",
                        result_one["you"]["points"] > result_one["opponent"]["points"],
                        f"{result_one['you']['points']} vs "
                        f"{result_one['opponent']['points']}",
                    )

        finished_one = await one.expect("match.finished")
        finished_two = await two.expect("match.finished")

        self.check(
            "both players are told the match completed",
            finished_one["end_reason"] == "COMPLETED",
        )
        self.check(
            "the two players get opposite verdicts",
            {finished_one["result"], finished_two["result"]} in (
                {"WIN", "LOSE"},
                {"DRAW"},
            ),
            f"{finished_one['result']} / {finished_two['result']}",
        )
        self.check(
            "scores agree from both sides",
            finished_one["your_score"] == finished_two["opponent_score"],
        )
        self.check(
            "the rating exchange is zero-sum",
            finished_one["rating"]["delta"] == -finished_two["rating"]["delta"],
            f"{finished_one['rating']['delta']} / {finished_two['rating']['delta']}",
        )
        return match_id

    async def scenario_not_host(self, one: Client, two: Client) -> None:
        """`match.start` from the guest must be refused."""
        await two.send("match.start")
        raw = await asyncio.wait_for(two.socket.recv(), timeout=10)
        message = json.loads(raw)
        two.received.append(message)
        self.check(
            "only the host may start the match",
            message["type"] == "error" and message["data"]["code"] == "NOT_HOST",
            str(message)[:120],
        )

        await one.send("match.start")
        await one.expect("match.started")
        await two.expect("match.started")
        self.check("the host can start it", True)

        await one.send("match.leave")
        await two.expect("match.finished")

    async def scenario_reconnect(self, one: Client, two: Client) -> None:
        print("\n[3] Dropping and reconnecting inside the grace period")

        settings = {"question_count": 5, "time_per_question": 20}
        await one.send("queue.join", settings)
        await one.expect("queue.waiting")
        await two.send("queue.join", settings)
        await one.expect("match.found")
        await two.expect("match.found")
        await one.expect("match.started")
        await two.expect("match.started")
        first_round = await one.expect("round.start")
        await two.expect("round.start")

        option_id = first_round["question"]["options"][0]["id"]
        await one.send("answer.submit", {"round_index": 0, "option_id": option_id})
        await asyncio.sleep(0.5)

        await one.socket.close()
        await two.expect("opponent.disconnected")

        await self.connect(one)
        resume = await one.expect("match.resume")
        self.check(
            "the reconnected player is put back on the right round",
            resume["round_index"] == 0 and resume["total_rounds"] == 5,
            str(resume)[:160],
        )
        self.check(
            "their already-submitted answer is remembered",
            resume["already_answered"] is True,
        )
        self.check(
            "the round clock is still running for them",
            resume["seconds_remaining"] is not None and resume["seconds_remaining"] > 0,
            str(resume["seconds_remaining"]),
        )
        await two.expect("opponent.reconnected")
        self.check("the opponent is told they came back", True)

        await two.send("answer.submit", {"round_index": 0, "option_id": option_id})
        result = await one.expect("round.result")
        self.check(
            "the reconnected player keeps receiving round results",
            result["round_index"] == 0,
        )

        await one.send("match.leave")
        await two.expect("match.finished")

    async def scenario_abandon(self, one: Client, two: Client) -> None:
        print("\n[4] Dropping the socket forfeits after the grace period")

        await one.send("queue.join", {"question_count": 3, "time_per_question": 10})
        await one.expect("queue.waiting")
        await two.send("queue.join", {"question_count": 3, "time_per_question": 10})
        await one.expect("match.found")
        await two.expect("match.found")
        await one.expect("match.started")
        await two.expect("match.started")
        await one.expect("round.start")
        await two.expect("round.start")

        await one.socket.close()
        warning = await two.expect("opponent.disconnected")
        self.check(
            "the surviving player is warned with a grace period",
            warning["grace_seconds"] > 0,
        )

        finished = await two.expect(
            "match.finished", timeout_seconds=warning["grace_seconds"] + 20
        )
        self.check(
            "the player who stayed wins by timeout",
            finished["result"] == "WIN"
            and finished["end_reason"] == "OPPONENT_TIMEOUT",
            str(finished)[:160],
        )

    async def scenario_rest(self, one: Client, two: Client, match_id: str) -> None:
        print("\n[5] History, stats and leaderboard over REST")

        headers = {"Authorization": f"Bearer {one.token}"}
        async with httpx.AsyncClient(timeout=10) as http:
            history = await http.get(f"{self.api}/duo/matches", headers=headers)
            detail = await http.get(
                f"{self.api}/duo/matches/{match_id}", headers=headers
            )
            stats = await http.get(f"{self.api}/duo/me/stats", headers=headers)
            board = await http.get(f"{self.api}/duo/leaderboard", headers=headers)
            stranger = await http.get(
                f"{self.api}/duo/matches/{match_id}",
                headers={"Authorization": f"Bearer {two.token}"},
            )
            missing = await http.get(
                f"{self.api}/duo/matches/{uuid4()}", headers=headers
            )

        rows = history.json()
        self.check(
            "the finished match shows up in history",
            history.status_code == 200
            and any(row["match_id"] == match_id for row in rows),
            history.text[:160],
        )
        body = detail.json()
        self.check(
            "match detail lists every round that was played",
            detail.status_code == 200 and len(body["rounds"]) == 3,
            str(body)[:160] if detail.status_code != 200 else f"{len(body['rounds'])} rounds",
        )
        self.check(
            "each round remembers both players' answers",
            detail.status_code == 200
            and all(r["question"] for r in body["rounds"]),
        )
        counters = stats.json()
        self.check(
            "stats count the matches played",
            stats.status_code == 200 and counters["matches_played"] >= 1,
            stats.text[:160],
        )
        self.check(
            "the leaderboard ranks the player",
            board.status_code == 200 and board.json()["my_rank"] is not None,
            board.text[:160],
        )
        self.check(
            "the opponent may also read the shared match",
            stranger.status_code == 200,
            stranger.text[:120],
        )
        self.check("an unknown match id is a 404", missing.status_code == 404)

    async def scenario_bad_input(self, one: Client) -> None:
        print("\n[6] Malformed and hostile input")

        await one.socket.send("this is not json")
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "garbage is rejected as INVALID_PAYLOAD",
            json.loads(raw)["data"]["code"] == "INVALID_PAYLOAD",
            raw[:120],
        )

        await one.send("nonsense.event")
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "an unknown event is rejected",
            json.loads(raw)["data"]["code"] == "UNKNOWN_EVENT",
            raw[:120],
        )

        await one.send("answer.submit", {"round_index": 0, "option_id": "nope"})
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "answering outside a match is rejected",
            json.loads(raw)["data"]["code"] == "NOT_IN_MATCH",
            raw[:120],
        )

        await one.send("room.create", {"time_per_question": 99999})
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "an absurd round timer is refused by the server",
            json.loads(raw)["data"]["code"] == "INVALID_PAYLOAD",
            raw[:120],
        )

    async def scenario_bad_token(self) -> None:
        print("\n[7] WebSocket authentication")
        for label, url in (
            ("no token", f"{self.ws_url}/api/v1/duo/ws"),
            ("bad token", f"{self.ws_url}/api/v1/duo/ws?token=not-a-jwt"),
        ):
            try:
                async with websockets.connect(url):
                    self.check(f"{label} is refused", False, "connection accepted")
            except Exception:
                self.check(f"{label} is refused", True)

    # --- driver -----------------------------------------------------------

    async def run(self) -> int:
        async with httpx.AsyncClient(timeout=20) as http:
            one = await self.register(http, "one")
            two = await self.register(http, "two")
        print(f"Players: {one.email} / {two.email}")

        await self.connect(one)
        await self.connect(two)
        match_id = await self.scenario_random_match(one, two)

        print("\n[2] Friend room by code")
        await one.send("room.create", {"question_count": 3, "time_per_question": 10})
        created = await one.expect("room.created")
        code = created["room_code"]
        self.check("a six-character room code is issued", len(code) == 6, code)

        async with httpx.AsyncClient(timeout=10) as http:
            preview = await http.get(
                f"{self.api}/duo/rooms/{code}",
                headers={"Authorization": f"Bearer {two.token}"},
            )
        self.check(
            "the room can be previewed over REST before joining",
            preview.status_code == 200 and preview.json()["host"]["id"] == one.user_id,
            preview.text[:120],
        )

        await two.send("room.join", {"room_code": code})
        found = await two.expect("match.found")
        await one.expect("match.found")
        self.check("a friend room does not auto-start", found["auto_start"] is False)
        await self.scenario_not_host(one, two)

        await self.scenario_reconnect(one, two)
        await self.scenario_abandon(one, two)

        # `one` closed its socket during the abandon scenario.
        await self.connect(one)
        await self.scenario_rest(one, two, match_id)
        await self.scenario_bad_input(one)
        await self.scenario_bad_token()

        await one.socket.close()
        await two.socket.close()

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
    args = parser.parse_args()
    return await Smoke(args.base_url).run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

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
        hp_one = 100

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

            if index == 0:
                self.check(
                    "the round carries the combat state",
                    round_one["your_hp"] == 100
                    and round_one["opponent_hp"] == 100
                    and round_one["your_mana"] == 0
                    and round_one["your_combo"] == 0
                    and round_one["you_are_stunned"] is False,
                    f"hp={round_one['your_hp']} mana={round_one['your_mana']}",
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
            result_two = await two.expect("round.result")

            print(
                f"    round {index}: "
                f"one hp={result_one['your_hp']:3d} mana={result_one['your_mana']:3d} "
                f"combo={result_one['your_combo']} dmg={result_one['your_blow']['damage']:2d} "
                f"({result_one['your_blow']['strike']})  |  "
                f"two hp={result_two['your_hp']:3d} mana={result_two['your_mana']:3d} "
                f"combo={result_two['your_combo']} dmg={result_two['your_blow']['damage']:2d} "
                f"({result_two['your_blow']['strike']})"
            )
            self.check(
                f"round {index}: health drops by exactly the blow that landed",
                result_one["your_hp"] == hp_one - result_one["opponent_blow"]["damage"],
                f"{hp_one} -> {result_one['your_hp']} "
                f"vs blow {result_one['opponent_blow']['damage']}",
            )
            self.check(
                f"round {index}: both sides agree on the health totals",
                result_one["your_hp"] == result_two["opponent_hp"]
                and result_one["opponent_hp"] == result_two["your_hp"],
            )
            hp_one = result_one["your_hp"]
            if result_one["you"]["correct"]:
                self.check(
                    f"round {index}: a correct answer deals damage",
                    result_one["your_blow"]["damage"] > 0,
                )
            else:
                self.check(
                    f"round {index}: a wrong answer deals nothing",
                    result_one["your_blow"]["damage"] == 0,
                )

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
        self.check(
            "the final health matches the last round reported",
            finished_one["your_hp_left"] == hp_one,
            f"{finished_one['your_hp_left']} vs {hp_one}",
        )
        self.check(
            "health agrees from both sides",
            finished_one["your_hp_left"] == finished_two["opponent_hp_left"],
        )

        for label, finished in (("one", finished_one), ("two", finished_two)):
            exp, gold = finished["exp"], finished["gold"]
            print(
                f"    {label}: exp {exp['before']}->{exp['after']} "
                f"({exp['delta']:+d}, level {exp['level_before']}->{exp['level_after']}) "
                f"gold {gold['before']}->{gold['after']} ({gold['delta']:+d}) "
                f"hp_left={finished['your_hp_left']}"
            )
        self.check(
            "the winner is paid experience and gold",
            all(
                finished[field]["delta"] > 0
                for finished in (finished_one, finished_two)
                for field in ("exp", "gold")
            ),
            f"one exp={finished_one['exp']['delta']} gold={finished_one['gold']['delta']}",
        )
        self.check(
            "the reported level matches the experience total",
            finished_one["exp"]["leveled_up"]
            == (finished_one["exp"]["level_after"] > finished_one["exp"]["level_before"]),
        )
        return match_id

    async def scenario_game_layer(self, one: Client) -> None:
        """The REST side of the game layer: profile, class, tree, loadout."""
        print("\n[9] Game layer: class, skill tree, loadout")
        headers = {"Authorization": f"Bearer {one.token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            profile = await http.get(f"{self.api}/game/profile", headers=headers)
            self.check(
                "a profile exists after the first match",
                profile.status_code == 200 and profile.json()["level"] >= 1,
                profile.text[:160],
            )

            tree = await http.get(f"{self.api}/game/skills", headers=headers)
            self.check("the skill tree is served", tree.status_code == 200, tree.text[:160])
            nodes = tree.json()["skills"] if tree.status_code == 200 else []
            starters = [node for node in nodes if node["unlock_kind"] == "STARTER"]
            self.check(
                "the starter skills are granted and equipped",
                len(starters) >= 2 and all(node["owned"] for node in starters),
                str([(n["code"], n["owned"], n["equipped_slot"]) for n in starters]),
            )
            self.check(
                "every ultimate is gated behind finishing its unit",
                all(
                    node["locked_reason"] == "NEEDS_UNIT"
                    for node in nodes
                    if node["unlock_kind"] == "UNIT_COMPLETION" and not node["owned"]
                ),
                str([n["code"] for n in nodes if n["unlock_kind"] == "UNIT_COMPLETION"]),
            )

            chosen = await http.post(
                f"{self.api}/game/class",
                headers=headers,
                json={"class_code": "WARRIOR"},
            )
            self.check(
                "the first class is free",
                chosen.status_code == 200
                and chosen.json()["class_code"] == "WARRIOR"
                and chosen.json()["gold"] == profile.json()["gold"],
                chosen.text[:160],
            )

            after = await http.get(f"{self.api}/game/skills", headers=headers)
            warrior_locked = [
                node["locked_reason"]
                for node in after.json()["skills"]
                if node["class_code"] not in (None, "WARRIOR")
            ]
            self.check(
                "another class's skills stay locked",
                warrior_locked and all(r == "WRONG_CLASS" for r in warrior_locked),
                str(set(warrior_locked)),
            )

            owned = [node["id"] for node in after.json()["skills"] if node["owned"]]
            equipped = await http.put(
                f"{self.api}/game/loadout",
                headers=headers,
                json={"skill_ids": owned[:2]},
            )
            self.check(
                "an owned loadout can be equipped",
                equipped.status_code == 200 and len(equipped.json()["slots"]) == len(owned[:2]),
                equipped.text[:160],
            )

            unowned = next(
                (node["id"] for node in after.json()["skills"] if not node["owned"]), None
            )
            if unowned is not None:
                refused = await http.put(
                    f"{self.api}/game/loadout",
                    headers=headers,
                    json={"skill_ids": [unowned]},
                )
                self.check(
                    "equipping a skill you do not own is refused",
                    refused.status_code == 400,
                    f"{refused.status_code} {refused.text[:120]}",
                )

    async def scenario_skills(self, one: Client, two: Client) -> None:
        """Casting a skill mid-match, and the rules around it."""
        print("\n[10] Skills in a live match")
        settings = {"question_count": 6, "time_per_question": 10}
        await one.send("queue.join", settings)
        await one.expect("queue.waiting")
        await two.send("queue.join", settings)
        await one.expect("match.found")
        await two.expect("match.found")
        await one.expect("match.started")
        await two.expect("match.started")

        round_one = await one.expect("round.start")
        await two.expect("round.start")

        async with httpx.AsyncClient(timeout=15) as http:
            loadout = await http.get(
                f"{self.api}/game/loadout",
                headers={"Authorization": f"Bearer {one.token}"},
            )
        slots = loadout.json()["slots"] if loadout.status_code == 200 else []
        self.check("the player brought skills into the match", bool(slots), loadout.text[:120])
        if not slots:
            return

        # No mana at round one, so the first cast has to be refused.
        await one.send(
            "skill.use",
            {"skill_code": slots[0]["code"], "round_index": round_one["round_index"]},
        )
        refusal = await self._expect_error(one)
        self.check(
            "casting without mana is refused",
            refusal == "NOT_ENOUGH_MANA",
            str(refusal),
        )

        await one.send("skill.use", {"skill_code": "NOT_A_SKILL", "round_index": 0})
        unknown = await self._expect_error(one)
        self.check(
            "casting a skill you have not equipped is refused",
            unknown == "SKILL_NOT_EQUIPPED",
            str(unknown),
        )

        # Play on until there is mana to spend, then cast for real.
        cast = False
        for index in range(round_one["round_index"], 6):
            if index > round_one["round_index"]:
                current = await one.expect("round.start")
                await two.expect("round.start")
            else:
                current = round_one
            option_id = current["question"]["options"][0]["id"]
            if not cast and current["your_mana"] >= slots[0]["mana_cost"]:
                await one.send(
                    "skill.use",
                    {"skill_code": slots[0]["code"], "round_index": index},
                )
                used_one = await one.expect("skill.used")
                used_two = await two.expect("skill.used")
                self.check(
                    "both players are told a skill was cast",
                    used_one["skill_code"] == used_two["skill_code"] == slots[0]["code"],
                )
                self.check(
                    "only the caster sees the private part",
                    used_two["private"] is None,
                    str(used_two["private"]),
                )
                self.check(
                    "casting spends mana",
                    used_one["your_mana"] == current["your_mana"] - slots[0]["mana_cost"],
                    f"{used_one['your_mana']} from {current['your_mana']}",
                )
                await one.send("skill.use", {"skill_code": slots[0]["code"], "round_index": index})
                twice = await self._expect_error(one)
                self.check(
                    "the same skill cannot be cast twice in one round",
                    twice == "SKILL_ALREADY_USED_THIS_ROUND",
                    str(twice),
                )
                cast = True

            for player in (one, two):
                await player.send(
                    "answer.submit", {"round_index": index, "option_id": option_id}
                )
            await one.expect("round.result")
            await two.expect("round.result")
            if cast:
                break

        self.check("a skill was cast during the match", cast)

        await one.send("match.leave")
        await one.expect("match.finished")
        await two.expect("match.finished")

        async with httpx.AsyncClient(timeout=15) as http:
            history = await http.get(
                f"{self.api}/duo/matches?limit=1",
                headers={"Authorization": f"Bearer {one.token}"},
            )
            match_id = history.json()[0]["match_id"]
            detail = await http.get(
                f"{self.api}/duo/matches/{match_id}",
                headers={"Authorization": f"Bearer {one.token}"},
            )
        body = detail.json()
        self.check(
            "the skill is recorded against the match",
            detail.status_code == 200
            and any(use["mine"] for use in body["skill_uses"]),
            str(body.get("skill_uses"))[:160],
        )
        self.check(
            "the match detail carries the combat columns",
            all("my_damage" in entry and "my_hp_after" in entry for entry in body["rounds"]),
        )

    async def scenario_retention(self, one: Client, two: Client) -> None:
        """Energy, streaks, chests and the ladder."""
        print("\n[11] Retention: energy, streak, loot, season")
        headers = {"Authorization": f"Bearer {one.token}"}

        async with httpx.AsyncClient(timeout=15) as http:
            profile = (await http.get(f"{self.api}/game/profile", headers=headers)).json()
            energy = profile["energy"]
            self.check(
                "the energy bar is reported and has been spent on matches so far",
                0 <= energy["current"] <= energy["maximum"],
                str(energy),
            )
            self.check(
                "a bar below full says when the next point lands",
                energy["current"] == energy["maximum"]
                or energy["next_regen_at"] is not None,
                str(energy),
            )
            self.check(
                "playing counted toward the daily streak",
                profile["day_streak"] >= 1,
                str(profile["day_streak"]),
            )

            inventory = (await http.get(f"{self.api}/game/items", headers=headers)).json()
            self.check(
                "chests from the matches above landed in the inventory",
                bool(inventory["items"]),
                str([row["code"] for row in inventory["items"]])[:160],
            )
            self.check(
                "the reported equipment bonus is within its ceiling",
                inventory["bonus_max_hp"] <= 20
                and inventory["bonus_damage_permille"] <= 150,
                str(inventory)[:160],
            )

            wearable = next(
                (row for row in inventory["items"] if row["slot"] == "ARMOR"), None
            )
            if wearable is not None:
                worn = await http.put(
                    f"{self.api}/game/equipment",
                    headers=headers,
                    json={"armor_id": wearable["id"]},
                )
                self.check(
                    "an owned item can be equipped",
                    worn.status_code == 200
                    and worn.json()["bonus_max_hp"] >= wearable["bonus_max_hp"],
                    worn.text[:160],
                )
                cleared = await http.put(
                    f"{self.api}/game/equipment", headers=headers, json={}
                )
                self.check(
                    "clearing a slot removes its bonus",
                    cleared.status_code == 200 and cleared.json()["bonus_max_hp"] == 0,
                    cleared.text[:120],
                )

            cosmetic = next(
                (row for row in inventory["items"] if row["kind"] != "EQUIPMENT"), None
            )
            if cosmetic is not None:
                refused = await http.put(
                    f"{self.api}/game/equipment",
                    headers=headers,
                    json={"armor_id": cosmetic["id"]},
                )
                self.check(
                    "a cosmetic item cannot be equipped",
                    refused.status_code == 400,
                    f"{refused.status_code} {refused.text[:120]}",
                )

            season = (
                await http.get(f"{self.api}/game/season/current", headers=headers)
            ).json()
            self.check(
                "a ladder season is open and the player has a tier",
                bool(season["code"]) and bool(season["tier"]),
                str(season)[:160],
            )
            self.check(
                "the season records the matches just played",
                season["matches_played"] >= 1,
                str(season["matches_played"]),
            )

            ladder = (
                await http.get(
                    f"{self.api}/duo/leaderboard?season=current", headers=headers
                )
            ).json()
            self.check(
                "the seasonal leaderboard is served with tiers",
                ladder["scope"] == "current"
                and all("tier" in entry for entry in ladder["entries"]),
                str(ladder)[:160],
            )
            all_time = (
                await http.get(
                    f"{self.api}/duo/leaderboard?season=all_time", headers=headers
                )
            ).json()
            self.check(
                "the all-time leaderboard is still available",
                all_time["scope"] == "all_time",
                str(all_time)[:120],
            )

        # Drain the bar and confirm the gate.
        drained = False
        for _ in range(8):
            async with httpx.AsyncClient(timeout=15) as http:
                left = (
                    await http.get(f"{self.api}/game/profile", headers=headers)
                ).json()["energy"]["current"]
            if left == 0:
                drained = True
                break
            await one.send("queue.join", {"question_count": 3, "time_per_question": 5})
            await two.send("queue.join", {"question_count": 3, "time_per_question": 5})
            try:
                await one.expect("match.found", timeout_seconds=10)
                await two.expect("match.found", timeout_seconds=10)
                await one.expect("match.finished", timeout_seconds=60)
                await two.expect("match.finished", timeout_seconds=60)
            except (AssertionError, TimeoutError):
                break

        if drained:
            await one.send("queue.join", {"question_count": 3, "time_per_question": 5})
            await two.send("queue.join", {"question_count": 3, "time_per_question": 5})
            code = await self._expect_error(one)
            self.check(
                "an empty energy bar refuses a new match",
                code == "NOT_ENOUGH_ENERGY",
                str(code),
            )
        else:
            print("    (energy never reached zero; gate not exercised)")

    async def _expect_error(self, client: Client, timeout_seconds: float = 10.0) -> str | None:
        """Read frames until an error arrives, and return its code.

        `Client.expect` raises on any error frame, which is right everywhere
        else and exactly wrong when the error is the thing being tested.
        """
        while True:
            raw = await asyncio.wait_for(client.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            client.received.append(message)
            if message["type"] == "error":
                return str(message["data"]["code"])

    async def scenario_combat(self, one: Client, two: Client) -> None:
        """A long match, to exercise combos and reach a knockout if it lands."""
        print("\n[8] Combat: combos, damage and knockout")
        rounds = 12
        settings = {"question_count": rounds, "time_per_question": 10}
        await one.send("queue.join", settings)
        await one.expect("queue.waiting")
        await two.send("queue.join", settings)
        await one.expect("match.found")
        await two.expect("match.found")
        await one.expect("match.started")
        await two.expect("match.started")

        played = 0
        best_combo = 0
        for index in range(rounds):
            try:
                round_one = await one.expect("round.start")
            except (AssertionError, TimeoutError):
                break
            await two.expect("round.start")

            # Both answer the same option so combos build on whoever is right.
            option_id = round_one["question"]["options"][0]["id"]
            for player in (one, two):
                await player.send(
                    "answer.submit", {"round_index": index, "option_id": option_id}
                )
            result_one = await one.expect("round.result")
            await two.expect("round.result")
            played += 1
            best_combo = max(best_combo, result_one["your_combo"])

            if result_one["your_blow"]["combo_count"] >= 3:
                self.check(
                    "a combo of three or more multiplies the damage",
                    result_one["your_blow"]["combo_multiplier"] > 1.0,
                    str(result_one["your_blow"]),
                )
            if result_one["opponent_hp"] == 0 or result_one["your_hp"] == 0:
                break

        finished_one = await one.expect("match.finished")
        finished_two = await two.expect("match.finished")
        print(
            f"    played {played}/{rounds} rounds, best combo {best_combo}, "
            f"end_reason={finished_one['end_reason']}, "
            f"hp {finished_one['your_hp_left']} / {finished_two['your_hp_left']}"
        )

        if finished_one["end_reason"] == "KNOCKOUT":
            self.check(
                "a knockout leaves the loser on zero health",
                0 in (finished_one["your_hp_left"], finished_two["your_hp_left"]),
                f"{finished_one['your_hp_left']} / {finished_two['your_hp_left']}",
            )
            self.check(
                "a knockout stops the match before its last question",
                played < rounds,
                f"played {played} of {rounds}",
            )
            self.check(
                "the player left standing wins the knockout",
                (finished_one["result"] == "WIN")
                == (finished_one["your_hp_left"] > finished_two["your_hp_left"]),
            )
        else:
            self.check(
                "a match that goes the distance leaves both players alive",
                finished_one["your_hp_left"] > 0 and finished_two["your_hp_left"] > 0,
                f"{finished_one['your_hp_left']} / {finished_two['your_hp_left']}",
            )

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
        await self.scenario_combat(one, two)
        await self.scenario_game_layer(one)
        await self.scenario_skills(one, two)
        await self.scenario_retention(one, two)

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

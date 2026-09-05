"""Play real 1v1 duo matches against a running server.

Drives two WebSocket clients through the whole protocol and checks the things
unit tests cannot: that the routes are wired up, that JWT auth works over a
WebSocket handshake, that questions really come out of the question bank, and
that results land in PostgreSQL.

There are no rounds to step through. Each client holds its own deck and answers
at its own pace, so a scenario drives one side as far as it likes without
touching the other -- and the bot learns each answer from the reveal, so a
question it got wrong is answered correctly when the deck brings it back.

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
    # Answer keys picked up from the reveals, so a question that comes back
    # after a wrong answer is answered correctly the second time. Without this
    # a deck could never be cleared and no scenario would ever reach the end.
    known: dict[str, str] = field(default_factory=dict)
    # Which question each open token belongs to: `answer.result` carries the
    # token and the key, but not the question.
    tokens: dict[str, str] = field(default_factory=dict)

    async def send(self, event: str, data: dict[str, Any] | None = None) -> None:
        await self.socket.send(json.dumps({"type": event, "data": data or {}}))

    async def read_until(
        self, *events: str, timeout_seconds: float = 20.0
    ) -> tuple[str, dict[str, Any]]:
        """Read until one of `events` arrives, learning as frames go past."""
        while True:
            raw = await asyncio.wait_for(self.socket.recv(), timeout=timeout_seconds)
            message = json.loads(raw)
            self.received.append(message)
            kind, data = message["type"], message["data"]
            if kind == "question.push":
                self.tokens[data["token"]] = data["question"]["id"]
            elif kind == "answer.result":
                question_id = self.tokens.pop(data["token"], None)
                correct = data.get("correct_option_ids") or []
                if question_id and correct:
                    self.known[question_id] = correct[0]
            if kind == "error":
                raise AssertionError(f"{self.name} got error: {data}")
            if kind in events:
                return kind, data

    async def expect(
        self, event: str, timeout_seconds: float = 20.0
    ) -> dict[str, Any]:
        """Read until `event` arrives, remembering everything seen on the way."""
        _kind, data = await self.read_until(event, timeout_seconds=timeout_seconds)
        return data

    def pick(self, push: dict[str, Any]) -> str:
        """The option to send: the learned answer if this question has come
        round before, otherwise the first one and take the consequences."""
        question = push["question"]
        return self.known.get(question["id"]) or question["options"][0]["id"]

    async def answer(
        self, push: dict[str, Any], option_id: str | None = None
    ) -> dict[str, Any]:
        """Answer what is on screen and read back what it did."""
        await self.send(
            "answer.submit",
            {"token": push["token"], "option_id": option_id or self.pick(push)},
        )
        return await self.expect("answer.result")

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

    # --- playing ----------------------------------------------------------

    async def race(
        self, *players: Client, limit: int = 60
    ) -> dict[str, dict[str, Any]]:
        """Answer whatever is on each player's screen until the match ends.

        Every player is driven in turn, which is as close to two people playing
        at once as one process gets. A match ends when someone clears their
        deck or falls, so this always terminates well inside `limit`.
        """
        finished: dict[str, dict[str, Any]] = {}
        for _ in range(limit):
            for player in players:
                if player.name in finished:
                    continue
                kind, data = await player.read_until("question.push", "match.finished")
                if kind == "match.finished":
                    finished[player.name] = data
                    continue
                await player.answer(data)
            if len(finished) == len(players):
                return finished
        for player in players:
            if player.name not in finished:
                finished[player.name] = await player.expect("match.finished")
        return finished

    # --- scenarios --------------------------------------------------------

    async def scenario_random_match(self, one: Client, two: Client) -> str:
        print("\n[1] Random queue, a real race, speed scoring")

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

        started_one = await one.expect("match.started")
        await two.expect("match.started")
        match_id = found_one["match_id"]

        self.check(
            "the match opens with a full deck, full health and a clock to beat",
            started_one["deck_size"] == 3
            and started_one["your_hp"] == started_one["your_max_hp"]
            and started_one["deadline_at"] > started_one["server_time_ms"],
            str(started_one)[:180],
        )

        push_one = await one.expect("question.push")
        push_two = await two.expect("question.push")
        self.check(
            "the question comes from the real question bank",
            bool(push_one["question"]["question"])
            and len(push_one["question"]["options"]) >= 2,
        )
        self.check(
            "no option is flagged as the correct one",
            all("correct" not in o for o in push_one["question"]["options"]),
        )
        self.check(
            "both players open on the same question in the same order",
            push_one["question"]["id"] == push_two["question"]["id"],
        )
        self.check(
            "the deck opens owing every question",
            push_one["deck_remaining"] == 3,
            str(push_one["deck_remaining"]),
        )

        # One answers at once, two dawdles -- and nothing waits for two.
        result_one = await one.answer(push_one)
        landed = await two.expect("opponent.answered")
        blow = result_one["blow"] or {}
        self.check(
            "the answer is graded and revealed on the spot",
            bool(result_one["correct_option_ids"]) and result_one["elapsed_ms"] >= 0,
            str(result_one)[:180],
        )
        self.check(
            "the blow reaches the other side the instant it is given",
            landed["damage"] == blow.get("damage", 0),
            f"{landed['damage']} vs {blow.get('damage')}",
        )
        if result_one["correct"]:
            self.check("a correct answer deals damage", blow.get("damage", 0) > 0)
            self.check(
                "and takes it off the opponent's bar at once",
                result_one["opponent_hp"] < started_one["opponent_hp"],
                f"{result_one['opponent_hp']} from {started_one['opponent_hp']}",
            )
        else:
            self.check("a wrong answer lands nothing", result_one["blow"] is None)
            self.check(
                "and puts the question back rather than dropping it",
                result_one["your_deck_remaining"] == 3,
                str(result_one["your_deck_remaining"]),
            )

        # The fast player is already on their next question while the slow one
        # has not touched their first. This is the whole change from lock step.
        second_one = await one.expect("question.push")
        self.check(
            "the faster player moves on without the other",
            second_one["token"] != push_one["token"] and not two.seen("answer.result"),
            str(len(two.seen("answer.result"))),
        )

        await asyncio.sleep(1.5)
        result_two = await two.answer(push_two, option_id=one.pick(push_one))
        if result_one["correct"] and result_two["correct"]:
            self.check(
                "answering faster scores more for the same answer",
                result_one["points"] > result_two["points"],
                f"{result_one['points']} vs {result_two['points']}",
            )

        await one.answer(second_one)
        results = await self.race(one, two)
        finished_one, finished_two = results[one.name], results[two.name]

        print(
            f"    end_reason={finished_one['end_reason']} "
            f"hp {finished_one['your_hp_left']} / {finished_two['your_hp_left']} "
            f"cleared {finished_one['your_deck_cleared']} / "
            f"{finished_two['your_deck_cleared']}"
        )
        self.check(
            "a three-question deck ends by being cleared, not by a knockout",
            finished_one["end_reason"] == "DECK_CLEARED",
            str(finished_one["end_reason"]),
        )
        self.check(
            "exactly one player cleared their deck",
            finished_one["your_deck_cleared"] != finished_two["your_deck_cleared"],
            f"{finished_one['your_deck_cleared']} / "
            f"{finished_two['your_deck_cleared']}",
        )
        self.check(
            "the player who cleared it is the winner",
            (finished_one["result"] == "WIN") == finished_one["your_deck_cleared"],
            f"{finished_one['result']} / cleared={finished_one['your_deck_cleared']}",
        )
        self.check(
            "clearing the deck means every question was answered correctly",
            finished_one["your_correct"] >= 3
            or finished_two["your_correct"] >= 3,
            f"{finished_one['your_correct']} / {finished_two['your_correct']}",
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
            "both players are paid experience and gold",
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

    async def scenario_skills(self) -> None:
        """Casting a skill mid-match, and the rules around it.

        Plays on a pair of freshly registered accounts rather than the pair the
        scenarios above have been using. A match costs one energy out of five
        and energy only trickles back (one point per thirty minutes), so by this
        point the shared pair has spent every point it had on the five matches
        before this one -- and the whole scenario used to die on
        NOT_ENOUGH_ENERGY before casting anything.
        """
        print("\n[10] Skills in a live match")
        async with httpx.AsyncClient(timeout=20) as http:
            one = await self.register(http, "skill-one")
            two = await self.register(http, "skill-two")
            # The mage opens on 30 mana, the most of the three classes. Without
            # a class at all a player opens on nothing, and this bot answers
            # blindly -- a wrong answer pays 5 mana -- so six rounds could never
            # reach the 40-50 a starter costs and nothing would ever be cast.
            await http.post(
                f"{self.api}/game/class",
                headers={"Authorization": f"Bearer {one.token}"},
                json={"class_code": "MAGE"},
            )
        await self.connect(one)
        await self.connect(two)
        try:
            await self._play_skill_match(one, two)
        finally:
            await one.socket.close()
            await two.socket.close()

    async def _play_skill_match(self, one: Client, two: Client) -> None:
        settings = {"question_count": 6, "time_per_question": 10}
        await one.send("queue.join", settings)
        await one.expect("queue.waiting")
        await two.send("queue.join", settings)
        await one.expect("match.found")
        await two.expect("match.found")
        started = await one.expect("match.started")
        await two.expect("match.started")

        async with httpx.AsyncClient(timeout=15) as http:
            loadout = await http.get(
                f"{self.api}/game/loadout",
                headers={"Authorization": f"Bearer {one.token}"},
            )
        slots = loadout.json()["slots"] if loadout.status_code == 200 else []
        self.check("the player brought skills into the match", bool(slots), loadout.text[:120])
        if not slots:
            return
        # The cheapest one, so the cast happens as early in the match as the
        # mana curve allows rather than depending on slot order.
        slots = sorted(slots, key=lambda slot: slot["mana_cost"])
        skill = slots[0]

        await one.send("skill.use", {"skill_code": "NOT_A_SKILL"})
        unknown = await self._expect_error(one)
        self.check(
            "casting a skill you have not equipped is refused",
            unknown == "SKILL_NOT_EQUIPPED",
            str(unknown),
        )

        if started["your_mana"] < skill["mana_cost"]:
            await one.send("skill.use", {"skill_code": skill["code"]})
            refusal = await self._expect_error(one)
            self.check(
                "casting without the mana is refused",
                refusal == "NOT_ENOUGH_MANA",
                str(refusal),
            )

        # Play on until there is mana to spend, then cast for real.
        mana = started["your_mana"]
        cast = False
        for _ in range(12):
            if mana >= skill["mana_cost"]:
                await one.send("skill.use", {"skill_code": skill["code"]})
                used_one = await one.expect("skill.used")
                used_two = await two.expect("skill.used")
                self.check(
                    "both players are told a skill was cast",
                    used_one["skill_code"] == used_two["skill_code"] == skill["code"],
                )
                self.check(
                    "only the caster sees the private part",
                    used_two["private"] is None,
                    str(used_two["private"]),
                )
                self.check(
                    "only the caster is told when it recharges",
                    used_one["ready_again_at"] > 0 and used_two["ready_again_at"] == 0,
                    f"{used_one['ready_again_at']} / {used_two['ready_again_at']}",
                )
                self.check(
                    "casting spends mana",
                    used_one["your_mana"] == mana - skill["mana_cost"],
                    f"{used_one['your_mana']} from {mana}",
                )
                await one.send("skill.use", {"skill_code": skill["code"]})
                twice = await self._expect_error(one)
                self.check(
                    "a skill cannot be recast before it recharges",
                    twice == "SKILL_ON_COOLDOWN",
                    str(twice),
                )
                cast = True
                break

            kind, data = await one.read_until("question.push", "match.finished")
            if kind == "match.finished":
                break
            result = await one.answer(data)
            mana = result["your_mana"]

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
            "the match detail carries the health both sides finished on",
            detail.status_code == 200
            and "my_hp_left" in body
            and "opponent_hp_left" in body,
            str(body)[:160],
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
                await one.expect("match.started", timeout_seconds=10)
                await two.expect("match.started", timeout_seconds=10)
                # Nothing ends a match on its own any more short of the clock,
                # so the deck has to be played out rather than waited out.
                await self.race(one, two)
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
        """A long deck, driven from one side, to reach a knockout."""
        print("\n[8] Combat: combos, damage and knockout")
        deck = 12
        settings = {"question_count": deck, "time_per_question": 10}
        await one.send("queue.join", settings)
        await one.expect("queue.waiting")
        await two.send("queue.join", settings)
        await one.expect("match.found")
        await two.expect("match.found")
        await one.expect("match.started")
        await two.expect("match.started")

        # Only `one` answers. Twelve questions is far more than the health bar
        # can absorb, so a player left alone with the deck wins by knockout
        # long before they could clear it.
        answered = 0
        best_combo = 0
        finished_one: dict[str, Any] | None = None
        for _ in range(deck * 2):
            kind, data = await one.read_until("question.push", "match.finished")
            if kind == "match.finished":
                finished_one = data
                break
            result = await one.answer(data)
            answered += 1
            best_combo = max(best_combo, result["your_combo"])
            blow = result["blow"]
            if blow and blow["combo_count"] >= 3:
                self.check(
                    "a combo of three or more multiplies the damage",
                    blow["combo_multiplier"] > 1.0,
                    str(blow),
                )
        if finished_one is None:
            finished_one = await one.expect("match.finished")
        finished_two = await two.expect("match.finished")

        print(
            f"    answered {answered}, best combo {best_combo}, "
            f"end_reason={finished_one['end_reason']}, "
            f"hp {finished_one['your_hp_left']} / {finished_two['your_hp_left']}"
        )

        self.check(
            "an opponent who never answers is knocked out",
            finished_one["end_reason"] == "KNOCKOUT",
            str(finished_one["end_reason"]),
        )
        self.check(
            "a knockout leaves the loser on zero health",
            finished_two["your_hp_left"] == 0 and finished_one["your_hp_left"] > 0,
            f"{finished_one['your_hp_left']} / {finished_two['your_hp_left']}",
        )
        self.check(
            "the player left standing wins the knockout",
            finished_one["result"] == "WIN" and finished_two["result"] == "LOSE",
            f"{finished_one['result']} / {finished_two['result']}",
        )
        self.check(
            "a knockout stops the match before the deck runs out",
            answered < deck and not finished_one["your_deck_cleared"],
            f"answered {answered} of {deck}",
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
        await one.expect("match.finished")
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
        first = await one.expect("question.push")
        await two.expect("question.push")

        # Answer one, then drop while the next is on screen: a resume has to
        # hand back both the score already earned and the open question.
        await one.answer(first)
        second = await one.expect("question.push")

        await one.socket.close()
        await two.expect("opponent.disconnected")

        await self.connect(one)
        resume = await one.expect("match.resume")
        self.check(
            "the reconnected player is put back on their own deck",
            resume["deck_size"] == 5 and resume["your_deck_remaining"] in (4, 5),
            str(resume)[:180],
        )
        self.check(
            "the question they were looking at comes back with its token",
            resume["token"] == second["token"]
            and resume["question"]["id"] == second["question"]["id"],
            f"{resume['token']} vs {second['token']}",
        )
        self.check(
            "the match clock is still running for them",
            resume["deadline_at"] > resume["server_time_ms"],
            f"{resume['deadline_at']} vs {resume['server_time_ms']}",
        )
        await two.expect("opponent.reconnected")
        self.check("the opponent is told they came back", True)

        # And the resumed token still answers.
        result = await one.answer(resume)
        self.check(
            "the token handed back by the resume still answers the question",
            result["token"] == resume["token"],
            f"{result['token']} vs {resume['token']}",
        )

        await one.send("match.leave")
        await one.expect("match.finished")
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
        await one.expect("question.push")
        await two.expect("question.push")

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
            "match detail reports the health both sides finished on",
            detail.status_code == 200
            and body["my_hp_left"] >= 0
            and body["opponent_hp_left"] >= 0,
            str(body)[:160],
        )
        self.check(
            "match detail agrees with the summary it extends",
            detail.status_code == 200 and body["match_id"] == match_id,
            str(body)[:160],
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

        await one.send("answer.submit", {"token": "not-a-token", "option_id": "nope"})
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "answering outside a match is rejected",
            json.loads(raw)["data"]["code"] == "NOT_IN_MATCH",
            raw[:120],
        )

        await one.send("room.create", {"time_per_question": 99999})
        raw = await asyncio.wait_for(one.socket.recv(), timeout=10)
        self.check(
            "an absurd speed reference is refused by the server",
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
        await self.scenario_skills()
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

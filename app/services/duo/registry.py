"""Process-local registry of live duo matches and the matchmaking queue.

Everything mutable is behind a single `asyncio.Lock` so two players pressing
"find match" in the same event-loop tick cannot both be paired with a third.

This module is the only place that knows the state is in-memory. Swapping it
for Redis later means reimplementing this class alone; the runtime, the event
schemas and the REST layer stay as they are.
"""

import asyncio
import secrets
import time

from app.services.duo.state import LiveMatch, QueueEntry

# Room codes avoid characters that are easy to misread out loud or in a chat.
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6

BASE_RATING_BAND = 200
BAND_WIDENING_PER_STEP = 50
BAND_WIDENING_INTERVAL_SECONDS = 5


def rating_band(waited_seconds: int) -> int:
    """How far apart two ratings may be after waiting this long.

    The band widens over time so a player with an unusual rating still gets a
    match instead of waiting forever.
    """
    steps = waited_seconds // BAND_WIDENING_INTERVAL_SECONDS
    return BASE_RATING_BAND + BAND_WIDENING_PER_STEP * steps


class DuoRegistry:
    def __init__(self) -> None:
        self._matches: dict[str, LiveMatch] = {}
        self._codes: dict[str, str] = {}
        self._user_match: dict[str, str] = {}
        self._queue: list[QueueEntry] = []
        # Callers hold this around any read-then-write sequence (pairing two
        # queued players, claiming a room slot). The methods below are plain
        # synchronous mutations and do not take it themselves.
        self.lock = asyncio.Lock()

    # --- queue -------------------------------------------------------------

    def is_queued(self, user_id: str) -> bool:
        return any(entry.user_id == user_id for entry in self._queue)

    def queue_position(self, user_id: str) -> int:
        for index, entry in enumerate(self._queue):
            if entry.user_id == user_id:
                return index + 1
        return 0

    def find_opponent(self, candidate: QueueEntry) -> QueueEntry | None:
        """First waiting entry compatible with `candidate`, or None.

        Compatible means identical settings — so both players agreed to the
        same round count, timer and filters — and ratings within whichever of
        the two bands is currently wider.
        """
        for entry in self._queue:
            if entry.user_id == candidate.user_id:
                continue
            if entry.settings != candidate.settings:
                continue
            band = max(
                rating_band(entry.waited_seconds()),
                rating_band(candidate.waited_seconds()),
            )
            if abs(entry.rating - candidate.rating) <= band:
                return entry
        return None

    def enqueue(self, entry: QueueEntry) -> None:
        self._queue.append(entry)

    def dequeue(self, user_id: str) -> QueueEntry | None:
        for index, entry in enumerate(self._queue):
            if entry.user_id == user_id:
                return self._queue.pop(index)
        return None

    def stale_queue_entries(self, max_wait_seconds: int) -> list[QueueEntry]:
        return [entry for entry in self._queue if entry.waited_seconds() >= max_wait_seconds]

    # --- matches -----------------------------------------------------------

    def new_room_code(self) -> str:
        """A code that is not currently taken by a live room."""
        while True:
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in self._codes:
                return code

    def register(self, match: LiveMatch) -> None:
        self._matches[match.match_id] = match
        if match.room_code is not None:
            self._codes[match.room_code] = match.match_id
        for user_id in match.players:
            self._user_match[user_id] = match.match_id

    def bind_player(self, match: LiveMatch, user_id: str) -> None:
        self._user_match[user_id] = match.match_id

    def unregister(self, match_id: str) -> LiveMatch | None:
        match = self._matches.pop(match_id, None)
        if match is None:
            return None
        if match.room_code is not None:
            self._codes.pop(match.room_code, None)
        for user_id in match.players:
            if self._user_match.get(user_id) == match_id:
                del self._user_match[user_id]
        return match

    def get(self, match_id: str) -> LiveMatch | None:
        return self._matches.get(match_id)

    def get_by_code(self, room_code: str) -> LiveMatch | None:
        match_id = self._codes.get(room_code.upper())
        return self._matches.get(match_id) if match_id else None

    def match_of_user(self, user_id: str) -> LiveMatch | None:
        match_id = self._user_match.get(user_id)
        return self._matches.get(match_id) if match_id else None

    def is_busy(self, user_id: str) -> bool:
        return user_id in self._user_match or self.is_queued(user_id)

    def all_matches(self) -> list[LiveMatch]:
        return list(self._matches.values())

    def age_seconds(self, match: LiveMatch) -> int:
        return int(time.monotonic() - match.created_at)


registry = DuoRegistry()

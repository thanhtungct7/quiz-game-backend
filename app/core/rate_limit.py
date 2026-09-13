"""In-process rate limiting: the counting, with no web framework in sight.

In-process is deliberate. This service already has to run as a single process
-- duo matches and lesson battles live in its memory -- so a shared store would
buy nothing yet. If that ever changes, the limiter is the part to move behind
Redis; the rules and the keys stay as they are.

Every clock here is injected, so the arithmetic can be tested without sleeping.
"""

import ipaddress
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class RateLimit:
    """At most `limit` hits per `window_seconds`."""

    limit: int
    window_seconds: float


@dataclass
class _Window:
    length: float
    start: float
    current: int
    previous: int


class SlidingWindowLimiter:
    """Sliding-window counter, one entry per key.

    Counts hits in fixed windows and weights the previous window by how much of
    it still overlaps the last `window_seconds`. That approximates a true
    sliding log in O(1) memory per key, without the burst a plain fixed window
    allows across a window boundary.

    Nothing here awaits, so on one event loop a `hit` is atomic without a lock.
    """

    PRUNE_EVERY = 1_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, _Window] = {}
        self._hits_since_prune = 0

    def hit(self, key: str, rule: RateLimit) -> int | None:
        """Count one hit. None when it is allowed; otherwise the whole seconds
        to wait before one would be, and the hit is not counted."""
        now = self._clock()
        length = rule.window_seconds
        start = math.floor(now / length) * length

        window = self._windows.get(key)
        if window is None or window.length != length:
            window = _Window(length=length, start=start, current=0, previous=0)
        elif window.start != start:
            just_before = math.isclose(start - window.start, length)
            window = _Window(
                length=length,
                start=start,
                current=0,
                previous=window.current if just_before else 0,
            )
        self._windows[key] = window

        elapsed = now - start
        weight = 1 - elapsed / length
        if window.previous * weight + window.current + 1 > rule.limit:
            return _retry_after(window, rule.limit, now)

        window.current += 1
        self._hits_since_prune += 1
        if self._hits_since_prune >= self.PRUNE_EVERY:
            self.prune()
        return None

    def prune(self) -> int:
        """Drop keys that cannot affect any future hit. Returns how many."""
        now = self._clock()
        stale = [
            key
            for key, window in self._windows.items()
            if now >= window.start + 2 * window.length
        ]
        for key in stale:
            del self._windows[key]
        self._hits_since_prune = 0
        return len(stale)

    def reset(self) -> None:
        self._windows.clear()
        self._hits_since_prune = 0

    def __len__(self) -> int:
        return len(self._windows)


def _retry_after(window: _Window, limit: int, now: float) -> int:
    """When the weighted count first leaves room for one more hit."""
    length = window.length
    if window.current + 1 > limit:
        # This window alone is full. Next window it becomes `previous`, and has
        # to decay far enough to fit one hit in beside it.
        into_next = length * (1 - (limit - 1) / window.current) if window.current else 0.0
        wait = (window.start + length - now) + into_next
    else:
        # The previous window is what is over; wait for its weight to decay.
        needed = length * (1 - (limit - 1 - window.current) / window.previous)
        wait = window.start + needed - now
    return max(1, math.ceil(wait))


class TokenBucket:
    """Per-connection flood control: `rate` tokens a second, up to `burst`.

    For the frames of one WebSocket, which need no key and no sharing -- the
    bucket lives exactly as long as the socket does.
    """

    def __init__(
        self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


def parse_networks(values: Sequence[str]) -> list[IPNetwork]:
    """IPs or CIDRs, as networks. Raises ValueError on anything else."""
    return [ipaddress.ip_network(value.strip(), strict=False) for value in values]


def client_ip(
    peer: str | None, forwarded_for: str | None, trusted: Sequence[IPNetwork]
) -> str:
    """The address a request should be counted against.

    `X-Forwarded-For` is only believed when the peer itself is a trusted proxy
    -- otherwise any client could claim any address and never be limited. Even
    then, the header is read from the right: every trusted proxy appends the
    address it saw, so the first hop from the right that is *not* a trusted
    proxy is the real client, and anything to its left is whatever that client
    chose to send.
    """
    address = peer or "unknown"
    if not forwarded_for or not trusted or not _is_trusted(address, trusted):
        return address

    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, trusted):
            return hop
    # Every hop was a proxy: the leftmost is the furthest one seen.
    return hops[0] if hops else address


def _is_trusted(address: str, trusted: Sequence[IPNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in trusted)

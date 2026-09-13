import pytest

from app.core.rate_limit import (
    RateLimit,
    SlidingWindowLimiter,
    TokenBucket,
    client_ip,
    parse_networks,
)


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


FIVE_PER_MINUTE = RateLimit(limit=5, window_seconds=60)


def test_hits_are_allowed_up_to_the_limit_then_refused() -> None:
    clock = FakeClock(1_200.0)  # the start of a window
    limiter = SlidingWindowLimiter(clock)

    assert [limiter.hit("k", FIVE_PER_MINUTE) for _ in range(5)] == [None] * 5
    assert limiter.hit("k", FIVE_PER_MINUTE) is not None


def test_keys_are_counted_separately() -> None:
    limiter = SlidingWindowLimiter(FakeClock(1_200.0))
    for _ in range(5):
        limiter.hit("a", FIVE_PER_MINUTE)

    assert limiter.hit("b", FIVE_PER_MINUTE) is None


def test_a_refused_hit_is_not_counted() -> None:
    clock = FakeClock(1_200.0)
    limiter = SlidingWindowLimiter(clock)
    for _ in range(5):
        limiter.hit("k", FIVE_PER_MINUTE)
    for _ in range(50):
        limiter.hit("k", FIVE_PER_MINUTE)

    # Two whole windows later nothing of the burst is left, refused hits included.
    clock.now += 120
    assert [limiter.hit("k", FIVE_PER_MINUTE) for _ in range(5)] == [None] * 5


def test_the_previous_window_still_weighs_on_the_next() -> None:
    # A plain fixed window would allow 5 at 1259s and 5 more at 1260s.
    clock = FakeClock(1_259.0)
    limiter = SlidingWindowLimiter(clock)
    for _ in range(5):
        limiter.hit("k", FIVE_PER_MINUTE)

    clock.now = 1_260.0
    assert limiter.hit("k", FIVE_PER_MINUTE) is not None

    # Half way through, half of the previous five still count: 2.5 + 2 new + 1 > 5.
    clock.now = 1_290.0
    assert limiter.hit("k", FIVE_PER_MINUTE) is None
    assert limiter.hit("k", FIVE_PER_MINUTE) is None
    assert limiter.hit("k", FIVE_PER_MINUTE) is not None


def test_retry_after_is_when_a_hit_would_first_be_allowed() -> None:
    clock = FakeClock(1_200.0)
    limiter = SlidingWindowLimiter(clock)
    for _ in range(5):
        limiter.hit("k", FIVE_PER_MINUTE)

    retry = limiter.hit("k", FIVE_PER_MINUTE)

    assert retry is not None
    clock.now += retry - 1
    assert limiter.hit("k", FIVE_PER_MINUTE) is not None
    clock.now += 1
    assert limiter.hit("k", FIVE_PER_MINUTE) is None


def test_retry_after_is_never_below_one_second() -> None:
    clock = FakeClock(1_259.9)
    limiter = SlidingWindowLimiter(clock)
    one = RateLimit(limit=1, window_seconds=60)
    limiter.hit("k", one)

    assert (limiter.hit("k", one) or 0) >= 1


def test_pruning_drops_only_keys_that_can_no_longer_matter() -> None:
    clock = FakeClock(1_200.0)
    limiter = SlidingWindowLimiter(clock)
    limiter.hit("old", FIVE_PER_MINUTE)
    clock.now = 1_320.0
    limiter.hit("fresh", FIVE_PER_MINUTE)

    assert limiter.prune() == 1
    assert len(limiter) == 1


def test_the_bucket_allows_a_burst_then_refills_at_its_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=2, burst=3, clock=clock)

    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    clock.now += 0.5
    assert bucket.take() is True
    assert bucket.take() is False


PROXY = parse_networks(["172.18.0.0/16"])


def test_the_peer_is_the_client_when_no_proxy_is_trusted() -> None:
    assert client_ip("203.0.113.9", "1.2.3.4", []) == "203.0.113.9"


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored() -> None:
    assert client_ip("203.0.113.9", "1.2.3.4", PROXY) == "203.0.113.9"


def test_the_real_client_is_read_behind_a_trusted_proxy() -> None:
    assert client_ip("172.18.0.5", "198.51.100.7", PROXY) == "198.51.100.7"


def test_a_spoofed_leftmost_hop_does_not_escape_the_limit() -> None:
    # The client sent "1.2.3.4"; the proxy appended the address it actually saw.
    assert client_ip("172.18.0.5", "1.2.3.4, 198.51.100.7", PROXY) == "198.51.100.7"


def test_chained_trusted_proxies_are_skipped() -> None:
    assert client_ip("172.18.0.5", "198.51.100.7, 172.18.0.9", PROXY) == "198.51.100.7"


def test_a_missing_peer_is_still_a_key() -> None:
    assert client_ip(None, None, PROXY) == "unknown"


def test_proxy_settings_must_be_addresses() -> None:
    with pytest.raises(ValueError):
        parse_networks(["not-an-ip"])

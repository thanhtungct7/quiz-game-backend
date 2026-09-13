from collections.abc import Iterator

import pytest

from app.api.rate_limit import limiter


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    """The limiter is process-wide; without this, one test's requests would
    count against the next test's allowance."""
    limiter.reset()
    yield
    limiter.reset()

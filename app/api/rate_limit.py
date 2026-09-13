"""Rate limiting, as FastAPI sees it: dependencies, a middleware hook, and the
WebSocket checks. The counting itself is `app/core/rate_limit.py`; the numbers
are `app/core/rate_limit_policies.py`.
"""

from typing import cast

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.params import Depends as DependsParam

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.core.rate_limit import RateLimit, SlidingWindowLimiter, TokenBucket, client_ip
from app.core.rate_limit_policies import (
    WS_CONNECT_PER_USER,
    WS_MESSAGE_BURST,
    WS_MESSAGES_PER_SECOND,
)

# One per process, which is the only kind of deployment this service supports.
limiter = SlidingWindowLimiter()


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many requests; try again in {retry_after} seconds")
        self.retry_after = retry_after


def check(key: str, rule: RateLimit) -> None:
    """Count one hit against `key`, raising RateLimitedError once it is over."""
    if not settings.rate_limit_enabled:
        return
    retry_after = limiter.hit(key, rule)
    if retry_after is not None:
        raise RateLimitedError(retry_after)


def enforce(key: str, rule: RateLimit) -> None:
    """`check`, as the 429 a route should answer with."""
    try:
        check(key, rule)
    except RateLimitedError as exc:
        raise too_many_requests(exc.retry_after) from exc


def too_many_requests(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many requests; try again in {retry_after} seconds",
        headers={"Retry-After": str(retry_after)},
    )


def request_ip(request: Request) -> str:
    return client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        settings.trusted_proxy_networks,
    )


def limit_by_ip(name: str, rule: RateLimit) -> DependsParam:
    """A route dependency limiting callers by address -- for routes with no account yet."""

    async def dependency(request: Request) -> None:
        enforce(f"{name}:ip:{request_ip(request)}", rule)

    return cast(DependsParam, Depends(dependency))


def limit_by_user(name: str, rule: RateLimit) -> DependsParam:
    """A route dependency limiting callers by account. Shares the request's
    `CurrentUser`, so the token is still only decoded once."""

    async def dependency(current_user: CurrentUser) -> None:
        enforce(f"{name}:user:{current_user.id}", rule)

    return cast(DependsParam, Depends(dependency))


async def admit_websocket(websocket: WebSocket, name: str, user_id: str) -> bool:
    """Whether to accept this socket. A refused one is closed with 1013 (try
    again later) before it is accepted, and the caller must simply return."""
    try:
        check(f"ws:{name}:user:{user_id}", WS_CONNECT_PER_USER)
    except RateLimitedError:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return False
    return True


class FrameThrottle:
    """Flood control for the frames of one socket.

    `allow` is false for a frame over the budget. `should_warn` is true only for
    the first refused frame of a run, so a flooding client gets told once
    instead of being answered frame for frame.
    """

    def __init__(self) -> None:
        self._bucket = TokenBucket(rate=WS_MESSAGES_PER_SECOND, burst=WS_MESSAGE_BURST)
        self._warned = False

    def allow(self) -> bool:
        if not settings.rate_limit_enabled or self._bucket.take():
            self._warned = False
            return True
        return False

    def should_warn(self) -> bool:
        if self._warned:
            return False
        self._warned = True
        return True

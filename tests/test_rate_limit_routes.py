import httpx

from app.api.dependencies import get_auth_service, get_current_user
from app.api.rate_limit import limiter
from app.core import rate_limit_policies as limits
from app.core.exceptions import InvalidCredentialsError
from app.main import app
from app.models.auth.user import User


class RefusingAuthService:
    async def login(self, *, email: str, password: str) -> None:
        raise InvalidCredentialsError()


async def _post(path: str, json: dict[str, object], ip: str = "203.0.113.9") -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(ip, 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(f"/api/v1{path}", json=json)


async def test_login_is_refused_with_retry_after_once_over_the_limit() -> None:
    app.dependency_overrides[get_auth_service] = RefusingAuthService
    try:
        statuses = []
        for attempt in range(limits.LOGIN_PER_IP.limit + 1):
            response = await _post(
                "/auth/login", {"email": f"user{attempt}@example.com", "password": "wrong-pass"}
            )
            statuses.append(response.status_code)
    finally:
        app.dependency_overrides.clear()

    assert statuses[:-1] == [401] * limits.LOGIN_PER_IP.limit
    assert statuses[-1] == 429
    assert int(response.headers["Retry-After"]) >= 1
    # The request id and hardening headers still go out on a refusal.
    assert "X-Request-ID" in response.headers


async def test_one_account_is_limited_across_addresses() -> None:
    app.dependency_overrides[get_auth_service] = RefusingAuthService
    try:
        statuses = [
            (
                await _post(
                    "/auth/login",
                    {"email": "victim@example.com", "password": "wrong-pass"},
                    ip=f"198.51.100.{i}",
                )
            ).status_code
            for i in range(limits.LOGIN_PER_EMAIL.limit + 1)
        ]
    finally:
        app.dependency_overrides.clear()

    assert statuses == [401] * limits.LOGIN_PER_EMAIL.limit + [429]


async def test_another_address_keeps_its_own_allowance() -> None:
    app.dependency_overrides[get_auth_service] = RefusingAuthService
    try:
        for attempt in range(limits.LOGIN_PER_IP.limit + 1):
            await _post("/auth/login", {"email": f"a{attempt}@example.com", "password": "x" * 8})
        other = await _post(
            "/auth/login", {"email": "b@example.com", "password": "x" * 8}, ip="198.51.100.1"
        )
    finally:
        app.dependency_overrides.clear()

    assert other.status_code == 401


async def test_per_user_limits_follow_the_account() -> None:
    app.dependency_overrides[get_current_user] = lambda: User(id="user-1", email="u@example.com")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            codes = [
                (await client.get("/api/v1/duo/rooms/ABCDEF")).status_code
                for _ in range(limits.ROOM_PREVIEW_PER_USER.limit + 1)
            ]
    finally:
        app.dependency_overrides.clear()

    assert 429 not in codes[:-1]
    assert codes[-1] == 429


async def test_health_checks_are_never_limited() -> None:
    limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        codes = {
            (await client.get("/api/v1/health/live")).status_code
            for _ in range(limits.GLOBAL_PER_IP.limit + 5)
        }

    assert codes == {200}

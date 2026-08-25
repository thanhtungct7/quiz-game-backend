import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import EmailAlreadyExistsError
from app.core.security import verify_password
from app.models.auth.user import User
from app.services.content.admin_bootstrap_service import AdminSeedService, seed_first_admin


class FakeUserRepository:
    def __init__(self, user: User | None = None) -> None:
        self.users: dict[str, User] = {}
        if user is not None:
            self.users[user.email] = user

    async def create_user(self, user: User) -> User:
        if user.email in self.users:
            raise EmailAlreadyExistsError("Email is already registered")
        user.id = user.id or f"user-{len(self.users) + 1}"
        self.users[user.email] = user
        return user


@pytest.mark.asyncio
async def test_create_admin_creates_new_user() -> None:
    repo = FakeUserRepository()
    service = AdminSeedService(repo)  # type: ignore[arg-type]

    user = await service.create_admin(
        "Admin@Example.com",
        username="root",
        password="s3cret-pass",  # noqa: S106
    )

    assert user.email == "admin@example.com"
    assert user.username == "root"
    assert user.is_admin is True
    assert user.is_active is True
    assert user.hashed_password is not None
    assert user.hashed_password != "s3cret-pass"  # noqa: S105
    assert verify_password("s3cret-pass", user.hashed_password)


@pytest.mark.asyncio
async def test_create_admin_refuses_to_touch_existing_account() -> None:
    existing = User(
        id="user-1",
        email="learner@example.com",
        hashed_password="already-hashed",  # noqa: S106
        is_admin=False,
        is_active=True,
    )
    repo = FakeUserRepository(existing)
    service = AdminSeedService(repo)  # type: ignore[arg-type]

    with pytest.raises(EmailAlreadyExistsError):
        await service.create_admin(
            "learner@example.com",
            username=None,
            password="another-pass",  # noqa: S106
        )

    # The existing account must be left completely untouched: no promotion.
    unchanged = repo.users["learner@example.com"]
    assert unchanged is existing
    assert unchanged.is_admin is False
    assert unchanged.hashed_password == "already-hashed"  # noqa: S105


@pytest.mark.asyncio
async def test_seed_first_admin_noop_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "first_admin_email", None)
    monkeypatch.setattr(settings, "first_admin_password", None)
    repo = FakeUserRepository()
    service = AdminSeedService(repo)  # type: ignore[arg-type]

    await seed_first_admin(service)

    assert repo.users == {}


@pytest.mark.asyncio
async def test_seed_first_admin_creates_when_configured_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "first_admin_email", "boss@example.com")
    monkeypatch.setattr(settings, "first_admin_password", SecretStr("longenoughpass"))
    monkeypatch.setattr(settings, "first_admin_username", "boss")
    repo = FakeUserRepository()
    service = AdminSeedService(repo)  # type: ignore[arg-type]

    await seed_first_admin(service)

    created = repo.users["boss@example.com"]
    assert created.is_admin is True
    assert created.username == "boss"


@pytest.mark.asyncio
async def test_seed_first_admin_skips_when_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "first_admin_email", "boss@example.com")
    monkeypatch.setattr(settings, "first_admin_password", SecretStr("longenoughpass"))
    existing = User(
        id="user-1",
        email="boss@example.com",
        hashed_password="already-hashed",  # noqa: S106
        is_admin=False,
        is_active=True,
    )
    repo = FakeUserRepository(existing)
    service = AdminSeedService(repo)  # type: ignore[arg-type]

    await seed_first_admin(service)  # must not raise

    unchanged = repo.users["boss@example.com"]
    assert unchanged is existing
    assert unchanged.is_admin is False

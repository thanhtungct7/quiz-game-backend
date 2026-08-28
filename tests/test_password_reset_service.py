from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import InvalidPasswordResetTokenError
from app.core.security import hash_password, hash_password_reset_token, verify_password
from app.models.auth.password_reset_token import PasswordResetToken
from app.models.auth.user import User
from app.services.auth.password_reset_service import PasswordResetService


class FakeUserRepository:
    def __init__(self, user: User | None = None) -> None:
        self.user = user

    async def get_user_by_email(self, email: str) -> User | None:
        if self.user is not None and self.user.email == email:
            return self.user
        return None

    async def get_by_id(self, user_id: str) -> User | None:
        if self.user is not None and self.user.id == user_id:
            return self.user
        return None


class FakeResetTokenRepository:
    def __init__(self) -> None:
        self.tokens: list[PasswordResetToken] = []

    async def replace_active_token(
        self, token: PasswordResetToken, revoked_at: datetime
    ) -> PasswordResetToken:
        for existing in self.tokens:
            if (
                existing.user_id == token.user_id
                and existing.used_at is None
                and existing.revoked_at is None
            ):
                existing.revoked_at = revoked_at
        self.tokens.append(token)
        return token

    async def get_by_hash_for_update(self, token_hash: str) -> PasswordResetToken | None:
        return next((t for t in self.tokens if t.token_hash == token_hash), None)


class FakeRefreshTokenRepository:
    def __init__(self) -> None:
        self.revoked_user_ids: list[str] = []

    async def revoke_all_for_user(self, user_id: str, revoked_at: datetime) -> None:
        del revoked_at
        self.revoked_user_ids.append(user_id)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class RecordingEmailService:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_password_reset(self, recipient: str, reset_token: str) -> None:
        self.sent.append((recipient, reset_token))


class FailingEmailService:
    async def send_password_reset(self, recipient: str, reset_token: str) -> None:
        del recipient, reset_token
        raise ConnectionRefusedError("SMTP is down")


def make_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "id": "user-123",
        "email": "player@example.com",
        "username": "player",
        "hashed_password": hash_password("old-password"),
        "is_active": True,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def make_service(
    user: User | None,
    email_service: object | None = None,
) -> tuple[PasswordResetService, FakeResetTokenRepository, FakeRefreshTokenRepository, object]:
    reset_tokens = FakeResetTokenRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    emails = email_service or RecordingEmailService()
    service = PasswordResetService(
        FakeSession(),  # type: ignore[arg-type]
        FakeUserRepository(user),  # type: ignore[arg-type]
        reset_tokens,  # type: ignore[arg-type]
        refresh_tokens,  # type: ignore[arg-type]
        emails,  # type: ignore[arg-type]
    )
    return service, reset_tokens, refresh_tokens, emails


@pytest.mark.asyncio
async def test_request_stores_only_the_token_hash_and_emails_the_raw_token() -> None:
    service, reset_tokens, _, emails = make_service(make_user())

    await service.request_password_reset("player@example.com")

    assert len(emails.sent) == 1  # type: ignore[attr-defined]
    recipient, raw_token = emails.sent[0]  # type: ignore[attr-defined]
    assert recipient == "player@example.com"
    stored = reset_tokens.tokens[0]
    assert stored.token_hash == hash_password_reset_token(raw_token)
    assert raw_token not in stored.token_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        make_user(is_active=False),
        # Google-only account: there is no password to reset.
        make_user(hashed_password=None, google_subject="google-sub-1"),
    ],
    ids=["unknown-email", "inactive-user", "google-only-user"],
)
async def test_request_stays_silent_for_accounts_that_cannot_be_reset(user: User | None) -> None:
    """No token, no email, no error -- otherwise the endpoint tells a caller
    which addresses have an account."""
    service, reset_tokens, _, emails = make_service(user)

    await service.request_password_reset("player@example.com")

    assert reset_tokens.tokens == []
    assert emails.sent == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_request_revokes_the_previous_unused_token() -> None:
    service, reset_tokens, _, emails = make_service(make_user())

    await service.request_password_reset("player@example.com")
    await service.request_password_reset("player@example.com")

    first, second = reset_tokens.tokens
    assert first.revoked_at is not None
    assert second.revoked_at is None

    # The superseded token is refused even though it was never used.
    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(emails.sent[0][1], "brand-new-password")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_request_swallows_a_failing_send_so_it_cannot_leak_which_emails_exist() -> None:
    service, reset_tokens, _, _ = make_service(make_user(), FailingEmailService())

    await service.request_password_reset("player@example.com")

    assert len(reset_tokens.tokens) == 1


@pytest.mark.asyncio
async def test_reset_sets_the_new_password_and_burns_the_token() -> None:
    user = make_user()
    service, reset_tokens, refresh_tokens, emails = make_service(user)
    await service.request_password_reset("player@example.com")
    raw_token = emails.sent[0][1]  # type: ignore[attr-defined]

    await service.reset_password(raw_token, "brand-new-password")

    assert verify_password("brand-new-password", user.hashed_password or "")
    assert not verify_password("old-password", user.hashed_password or "")
    assert reset_tokens.tokens[0].used_at is not None
    # A reset is what someone does when they suspect a compromise, so every
    # session that was open before it has to go.
    assert refresh_tokens.revoked_user_ids == [user.id]


@pytest.mark.asyncio
async def test_reset_refuses_a_token_that_was_already_used() -> None:
    service, _, _, emails = make_service(make_user())
    await service.request_password_reset("player@example.com")
    raw_token = emails.sent[0][1]  # type: ignore[attr-defined]
    await service.reset_password(raw_token, "brand-new-password")

    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(raw_token, "another-password")


@pytest.mark.asyncio
async def test_reset_refuses_an_expired_token() -> None:
    service, reset_tokens, _, emails = make_service(make_user())
    await service.request_password_reset("player@example.com")
    reset_tokens.tokens[0].expires_at = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(emails.sent[0][1], "brand-new-password")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_reset_refuses_an_unknown_token() -> None:
    service, _, _, _ = make_service(make_user())

    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password("not-a-real-token", "brand-new-password")

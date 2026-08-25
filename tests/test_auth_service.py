from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.exceptions import (
    AccountLinkRequiredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.security import hash_password, hash_refresh_token
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.schemas.auth.auth import RegisterRequest
from app.services.auth import auth_service as auth_service_module
from app.services.auth.auth_service import AuthService


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

    async def get_user_by_google_subject(self, subject: str) -> User | None:
        if self.user is not None and self.user.google_subject == subject:
            return self.user
        return None

    async def create_user(self, user: User) -> User:
        if user.id is None:
            user.id = "created-user-123"
        self.user = user
        return user


class FakeRefreshTokenRepository:
    def __init__(self) -> None:
        self.tokens: dict[str, RefreshToken] = {}

    async def get_refresh_token_by_hash(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> RefreshToken | None:
        del for_update
        return self.tokens.get(token_hash)

    async def create(self, token: RefreshToken) -> RefreshToken:
        self.tokens[token.token_hash] = token
        return token

    async def rotate(
        self,
        current_token: RefreshToken,
        replacement_token: RefreshToken,
        revoked_at: datetime,
    ) -> RefreshToken:
        current_token.revoked_at = revoked_at
        self.tokens[replacement_token.token_hash] = replacement_token
        return replacement_token

    async def revoke(self, token: RefreshToken, revoked_at: datetime) -> None:
        token.revoked_at = revoked_at


def make_service() -> tuple[AuthService, FakeRefreshTokenRepository]:
    user = User(
        id="user-123",
        email="player@example.com",
        username="player",
        hashed_password=hash_password("strong-password"),
        is_active=True,
    )
    refresh_tokens = FakeRefreshTokenRepository()
    service = AuthService(FakeUserRepository(user), refresh_tokens)  # type: ignore[arg-type]
    return service, refresh_tokens


@pytest.mark.asyncio
async def test_login_persists_hashed_refresh_token() -> None:
    service, refresh_tokens = make_service()

    response = await service.login("player@example.com", "strong-password")

    assert response.refresh_token not in refresh_tokens.tokens
    stored = refresh_tokens.tokens[hash_refresh_token(response.refresh_token)]
    assert stored.user_id == "user-123"
    assert stored.expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_rejects_reuse() -> None:
    service, refresh_tokens = make_service()
    first_pair = await service.login("player@example.com", "strong-password")
    old_token = refresh_tokens.tokens[hash_refresh_token(first_pair.refresh_token)]

    second_pair = await service.refresh(first_pair.refresh_token)

    assert second_pair.refresh_token != first_pair.refresh_token
    assert old_token.revoked_at is not None
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(first_pair.refresh_token)


@pytest.mark.asyncio
async def test_password_login_rejects_google_only_account() -> None:
    user = User(
        id="google-user-123",
        email="player@example.com",
        google_subject="google-subject-123",
        hashed_password=None,
        is_active=True,
    )
    service = AuthService(
        FakeUserRepository(user),  # type: ignore[arg-type]
        FakeRefreshTokenRepository(),  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidCredentialsError):
        await service.login("player@example.com", "any-password")


@pytest.mark.asyncio
async def test_google_login_creates_user_and_issues_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(_: str) -> dict[str, object]:
        return {
            "sub": "google-subject-123",
            "email": "Player@Example.com",
            "email_verified": True,
            "name": "  A player name that is safely normalized  ",
            "picture": "https://example.com/avatar.png",
        }

    monkeypatch.setattr(auth_service_module, "verify_google_id_token", fake_verify)
    users = FakeUserRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    service = AuthService(users, refresh_tokens)  # type: ignore[arg-type]

    response = await service.login_with_google("valid-google-id-token")

    assert users.user is not None
    assert users.user.email == "player@example.com"
    assert users.user.google_subject == "google-subject-123"
    assert users.user.hashed_password is None
    assert users.user.username == "A player name that is safely normalized"
    assert users.user.avatar_url == "https://example.com/avatar.png"
    assert hash_refresh_token(response.refresh_token) in refresh_tokens.tokens


@pytest.mark.asyncio
async def test_google_login_reuses_account_by_google_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(_: str) -> dict[str, object]:
        return {
            "sub": "google-subject-123",
            "email": "new-email@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(auth_service_module, "verify_google_id_token", fake_verify)
    user = User(
        id="google-user-123",
        email="old-email@example.com",
        google_subject="google-subject-123",
        hashed_password=None,
        is_active=True,
    )
    refresh_tokens = FakeRefreshTokenRepository()
    service = AuthService(
        FakeUserRepository(user),  # type: ignore[arg-type]
        refresh_tokens,  # type: ignore[arg-type]
    )

    await service.login_with_google("valid-google-id-token")

    assert len(refresh_tokens.tokens) == 1
    assert user.email == "old-email@example.com"


@pytest.mark.asyncio
async def test_google_login_does_not_auto_link_an_existing_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(_: str) -> dict[str, object]:
        return {
            "sub": "different-google-subject",
            "email": "player@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(auth_service_module, "verify_google_id_token", fake_verify)
    existing_user = User(
        id="local-user-123",
        email="player@example.com",
        hashed_password=hash_password("strong-password"),
        is_active=True,
    )
    service = AuthService(
        FakeUserRepository(existing_user),  # type: ignore[arg-type]
        FakeRefreshTokenRepository(),  # type: ignore[arg-type]
    )

    with pytest.raises(AccountLinkRequiredError):
        await service.login_with_google("valid-google-id-token")


def test_register_request_normalizes_fields() -> None:
    payload = RegisterRequest(
        email="Player@Example.com",
        password="strong-password",  # noqa: S106
        username="  player  ",
    )

    assert payload.email == "player@example.com"
    assert payload.username == "player"


def test_register_request_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="player@example.com", password="short")  # noqa: S106

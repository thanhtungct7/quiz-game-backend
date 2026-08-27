import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Sign a short-lived JWT carrying the user id as `sub`. Self-contained
    and stateless — verified by signature alone, no database lookup."""
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> str:
    """Verify signature and required claims, and return the subject (user id).

    Explicitly requires `type == "access"` so a refresh token — which is a
    random opaque string, not a JWT, but this guards the type confusion in
    general — can never be replayed here as an access token.
    """
    payload = jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "type", "iat", "exp"]},
    )
    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str) or not subject:
        raise jwt.InvalidTokenError("Invalid token claims")
    return subject


def create_refresh_token() -> str:
    """Opaque, high-entropy random token — unlike the access token, this is
    only meaningful looked up by its hash in the database, so it can be
    revoked."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # Deterministic hashing is safe because refresh tokens have 384 bits of entropy.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

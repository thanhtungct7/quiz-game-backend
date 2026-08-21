from datetime import timedelta

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_is_hashed_and_verifiable() -> None:
    password = "a-long-test-password"  # noqa: S105
    encoded = hash_password(password)

    assert encoded != password
    assert verify_password(password, encoded)
    assert not verify_password("wrong-password", encoded)


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123")
    assert decode_access_token(token) == "user-123"


def test_expired_access_token_is_rejected() -> None:
    token = create_access_token("user-123", expires_delta=timedelta(seconds=-1))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_refresh_token_hash_is_deterministic() -> None:
    token = "high-entropy-refresh-token"  # noqa: S105
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert hash_refresh_token(token) != token

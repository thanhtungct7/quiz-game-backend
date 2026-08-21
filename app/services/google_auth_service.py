from collections.abc import Mapping
from typing import Any

from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests
from google.oauth2 import id_token
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exceptions import GoogleAuthUnavailableError, InvalidGoogleTokenError

_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def _verify_token(id_token_str: str) -> Mapping[str, Any]:
    claims: Mapping[str, Any] = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
        id_token_str,
        requests.Request(),
        settings.google_web_client_id,
    )
    return claims


async def verify_google_id_token(id_token_str: str) -> Mapping[str, Any]:
    try:
        claims: Mapping[str, Any] = await run_in_threadpool(
            _verify_token,
            id_token_str,
        )
    except google_auth_exceptions.TransportError as exc:
        raise GoogleAuthUnavailableError(
            "Google authentication is temporarily unavailable"
        ) from exc
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        raise InvalidGoogleTokenError("Invalid or expired Google ID token") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise InvalidGoogleTokenError("Invalid Google token issuer")
    if claims.get("email_verified") is not True:
        raise InvalidGoogleTokenError("Google email is not verified")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise InvalidGoogleTokenError("Google token has an invalid subject")
    if not isinstance(email, str) or not email or len(email) > 320 or "@" not in email:
        raise InvalidGoogleTokenError("Google token has an invalid email")

    return claims

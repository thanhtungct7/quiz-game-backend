"""Conditional-GET support for the read-only course content endpoints.

Course content changes rarely but is fetched on every app launch, so these
responses carry an ETag derived from the payload itself. A client that sends
back the same `If-None-Match` gets an empty 304 instead of the body.

Hashing the serialised payload (rather than a stored version column) means the
ETag can never drift out of sync with what is actually returned.
"""

from hashlib import blake2b

from fastapi import Request, Response, status
from pydantic import BaseModel

# Content is public and identical for every user, but must still be revalidated
# so an edit in the admin API shows up without waiting out a TTL.
CONTENT_CACHE_CONTROL = "public, max-age=0, must-revalidate"


def _etag_for(body: bytes) -> str:
    return f'"{blake2b(body, digest_size=16).hexdigest()}"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: a comma-separated list, `*`, or weak comparison
    (a `W/` prefix is ignored when comparing)."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    candidates = (value.strip() for value in if_none_match.split(","))
    return any(candidate.removeprefix("W/") == etag for candidate in candidates)


def conditional_json_response(request: Request, payload: BaseModel) -> Response:
    """Serialise `payload` as a cacheable JSON response, or 304 if unchanged."""
    body = payload.model_dump_json(by_alias=True).encode()
    etag = _etag_for(body)
    headers = {"ETag": etag, "Cache-Control": CONTENT_CACHE_CONTROL}

    if _matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return Response(content=body, media_type="application/json", headers=headers)

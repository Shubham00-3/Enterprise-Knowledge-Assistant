"""Per-user authentication and the owner-id every request is scoped to.

When auth is disabled (the MVP default) all data belongs to a single seed user, so
the app keeps working exactly as before. When auth is enabled, the caller must present
a valid Supabase JWT and every document/query is scoped to that user's id.
"""

import jwt
from fastapi import Depends, Header, HTTPException

from app.config import SEED_OWNER_ID, Settings, get_settings

__all__ = ["SEED_OWNER_ID", "resolve_owner_id", "current_owner_id"]


def resolve_owner_id(authorization: str | None, settings: Settings) -> str:
    """Return the owner id this request's data should be scoped to.

    - auth disabled -> the seed owner (single shared pool, unchanged MVP behaviour).
    - auth enabled  -> the `sub` claim of a verified Supabase JWT, else HTTP 401.
    """
    if not settings.require_auth:
        return SEED_OWNER_ID
    if not settings.supabase_jwt_secret:
        raise HTTPException(status_code=500, detail="REQUIRE_AUTH is on but SUPABASE_JWT_SECRET is unset")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="Token has no subject")
    return subject


def current_owner_id(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """FastAPI dependency: the owner id the current request is allowed to read/write."""
    return resolve_owner_id(authorization, settings)

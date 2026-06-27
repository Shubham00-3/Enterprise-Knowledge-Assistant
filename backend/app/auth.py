"""Per-user authentication and the owner-id every request is scoped to.

When auth is disabled (the MVP default) all data belongs to a single seed user, so
the app keeps working exactly as before. When auth is enabled, the caller must present
a valid Supabase JWT and every document/query is scoped to that user's id.

Supabase now signs access tokens with asymmetric keys (ES256) published at a JWKS
endpoint; older projects use a shared HS256 secret. We support both: verify against the
JWKS when one is configured (or the token is asymmetric), otherwise fall back to the
legacy HS256 shared secret.
"""

from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from app.config import SEED_OWNER_ID, Settings, get_settings

__all__ = ["SEED_OWNER_ID", "resolve_owner_id", "current_owner_id"]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    """One client per JWKS url; it caches fetched signing keys across requests."""
    return PyJWKClient(url)


def _jwks_url(settings: Settings) -> str | None:
    """JWKS endpoint to verify asymmetric tokens against, if configured.

    Prefers an explicit SUPABASE_JWKS_URL, else derives it from SUPABASE_URL. Uses
    getattr so test doubles that omit these fields still work.
    """
    explicit = getattr(settings, "supabase_jwks_url", None)
    if explicit:
        return explicit
    base = getattr(settings, "supabase_url", None)
    if base:
        return base.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return None


def _decode_claims(token: str, settings: Settings) -> dict:
    jwks_url = _jwks_url(settings)
    algorithm = jwt.get_unverified_header(token).get("alg", "")
    # Use JWKS for asymmetric tokens (or whenever no HS256 secret is configured).
    if jwks_url is not None and (algorithm.startswith(("ES", "RS")) or not settings.supabase_jwt_secret):
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience=settings.supabase_jwt_audience,
        )
    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience=settings.supabase_jwt_audience,
    )


def resolve_owner_id(authorization: str | None, settings: Settings) -> str:
    """Return the owner id this request's data should be scoped to.

    - auth disabled -> the seed owner (single shared pool, unchanged MVP behaviour).
    - auth enabled  -> the `sub` claim of a verified Supabase JWT, else HTTP 401.
    """
    if not settings.require_auth:
        return SEED_OWNER_ID
    if _jwks_url(settings) is None and not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=500,
            detail="REQUIRE_AUTH is on but no token verifier is configured "
            "(set SUPABASE_URL/SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _decode_claims(token, settings)
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

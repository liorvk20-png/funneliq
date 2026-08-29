import os

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient

SUPABASE_URL = os.environ["SUPABASE_URL"]

# Supabase signs access tokens with asymmetric keys (the default for new
# projects) and publishes the public half here. There is no static JWT secret
# to hold: we fetch the signing key by the token's `kid` and verify against it.
# PyJWKClient caches fetched keys, so this is not a network call per request.
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True)


def get_current_user_token(authorization: str | None = Header(default=None)) -> str:
    """
    FastAPI dependency: verifies the caller's Supabase-issued JWT and returns
    the raw access token so it can be forwarded to a user-scoped Supabase
    client (see db.get_user_client) — belt-and-suspenders with RLS.
    Raises 401 if the header is missing, malformed, or the token is invalid/expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return token

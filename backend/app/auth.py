import binascii
import logging

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient

from app.config import require_env

log = logging.getLogger("funneliq.auth")

SUPABASE_URL = require_env("SUPABASE_URL")

# Supabase signs access tokens with asymmetric keys (the default for new
# projects) and publishes the public half here. There is no static JWT secret
# to hold: we fetch the signing key by the token's `kid` and verify against it.
# PyJWKClient caches fetched keys, so this is not a network call per request.
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True)


# The verified subject of each token this process has checked. Small and
# short-lived: tokens last an hour, and a restart empties it.
_SUBJECTS: dict[str, str | None] = {}


def user_id_for(token: str) -> str | None:
    """Which account a verified token belongs to."""
    return _SUBJECTS.get(token)


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

    # Fetching the key is a network call, and on a freshly started container it
    # is the first one — the cache is empty until an authenticated request
    # arrives. Treating a failed fetch as a bad token, which is what a single
    # except-everything clause here would do, signs the person out over a blip
    # on our side and tells them their session expired. It is a 503: come back
    # in a moment, and stay signed in.
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
    except jwt.exceptions.PyJWKClientConnectionError as exc:
        # First, and it must stay first: PyJWKClientConnectionError inherits
        # from PyJWTError, so a broader clause above it swallows a network
        # outage and reports it as a bad token -- signing the person out over a
        # fault on our side and telling them their session expired. Reordering
        # these two clauses reintroduces that, and the test suite says so.
        log.warning("JWKS fetch failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "לא הצלחנו לאמת את ההתחברות מול שרת ההזדהות. נסה שוב בעוד רגע.",
        ) from exc
    except (jwt.PyJWTError, ValueError, TypeError, binascii.Error) as exc:
        # Everything a malformed header can raise on the way to finding a key.
        # The list grew twice while being written: base64url_decode raises
        # binascii.Error for a segment that is not base64, and the splitter
        # raises DecodeError for a token that is not three segments -- and
        # DecodeError is not a ValueError, so a first fix that caught only the
        # value errors still returned 500 for "Bearer eyJhbGciOiJIUzI1NiJ9".
        log.info("malformed bearer token rejected: %s", exc)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired token"
        ) from None

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except (jwt.PyJWTError, ValueError, TypeError, binascii.Error):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired token"
        ) from None
    # The verified subject, cached on the token string. Callers that need to
    # know *which* member is asking read it from here rather than guessing from
    # a query result -- a colleague can legitimately read every profile in
    # their company, so "the first row" is not the caller.
    _SUBJECTS[token] = claims.get("sub")
    return token

import os
import jwt
from fastapi import Header, HTTPException, status

SUPABASE_JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]


def get_current_user_token(authorization: str = Header(...)) -> str:
    """
    FastAPI dependency: verifies the caller's Supabase-issued JWT and returns
    the raw access token so it can be forwarded to a user-scoped Supabase
    client (see db.get_user_client) — belt-and-suspenders with RLS.
    Raises 401 if the header is missing, malformed, or the token is invalid/expired.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return token

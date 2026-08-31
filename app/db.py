import os

from supabase import Client, create_client

from app.config import require_env

SUPABASE_URL = require_env("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = require_env("SUPABASE_PUBLISHABLE_KEY")
# Script-only. Deliberately not required at API runtime, so a misconfigured
# deploy fails loudly rather than quietly falling back to an RLS-bypassing key.
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")


def get_user_client(access_token: str) -> Client:
    """
    Client scoped to the signed-in user. Every request carries the user's own
    JWT to Supabase, so Row Level Security enforces access at the database —
    not app code deciding who gets which rows.
    """
    client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
    client.postgrest.auth(access_token)
    return client


def get_anon_client() -> Client:
    """
    Client with no user attached. Only for the auth calls that happen before
    anyone is signed in — sign-in and sign-up. It carries the publishable key,
    which grants nothing on its own, so a data read through it returns nothing
    but the empty set RLS allows an anonymous caller.
    """
    return create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)


def get_service_client() -> Client:
    """
    Secret-key client. Bypasses RLS entirely.

    Used by server-side scripts (the CSV loader), and by exactly one request
    handler: /api/login, to turn a company name into the address it belongs to.
    That call runs before anyone is signed in, so there is no RLS context for it
    to bypass, and the address it finds is used to authenticate and never
    returned to the caller. Any other use in request-handling code is a bug —
    a read on behalf of a signed-in user must go through get_user_client, so
    that the database decides what they may see.
    """
    if not SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY not set — this client is for scripts only.")
    return create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

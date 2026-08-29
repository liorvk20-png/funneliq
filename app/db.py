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


def get_service_client() -> Client:
    """
    Secret-key client. Bypasses RLS entirely. Used ONLY by server-side scripts
    (the CSV loader) — never by request-handling code in main.py.
    """
    if not SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY not set — this client is for scripts only.")
    return create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

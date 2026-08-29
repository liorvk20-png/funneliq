import os
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")  # script-only, not required at API runtime


def get_user_client(access_token: str) -> Client:
    """
    Client scoped to the signed-in user. Every request carries the user's own
    JWT to Supabase, so Row Level Security enforces access at the database —
    not app code deciding who gets which rows.
    """
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.postgrest.auth(access_token)
    return client


def get_service_client() -> Client:
    """
    Service-role client. Bypasses RLS entirely. Used ONLY by server-side
    scripts (the CSV loader) — never by request-handling code in main.py.
    """
    if not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY not set — this client is for scripts only.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

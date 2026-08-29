import os


class MissingConfig(RuntimeError):
    """Raised at import time when a required environment variable is absent."""


def require_env(name: str) -> str:
    """
    Read a required environment variable, or fail with a message that says what
    to do about it.

    Deliberately raises rather than falling back to a default: an app that comes
    up without its Supabase configuration would either serve nothing or, worse,
    look healthy while every query failed. Failing at import keeps a broken
    deploy visibly broken. The only thing wrong with the bare KeyError this
    replaces was that it named the variable without saying where it belongs.
    """
    value = os.environ.get(name)
    if not value:
        raise MissingConfig(
            f"Missing required environment variable {name}.\n"
            f"  - Deployed (Railway): add it under the service's Variables tab.\n"
            f"  - Local: add it to .env in the repo root (see .env.example).\n"
            f"Required: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY. "
            f"SUPABASE_SECRET_KEY is needed only by scripts/load_csv_to_supabase.py."
        )
    return value

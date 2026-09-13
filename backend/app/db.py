import os

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import require_env

SUPABASE_URL = require_env("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = require_env("SUPABASE_PUBLISHABLE_KEY")
# Script-only. Deliberately not required at API runtime, so a misconfigured
# deploy fails loudly rather than quietly falling back to an RLS-bypassing key.
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")

# Retried only where a replay cannot duplicate work. A dropped connection tells
# us the response never arrived; it does not tell us whether the server acted.
# For a read that distinction does not matter, and for POST/PATCH it is the
# difference between a retry and a second upload.
_REPLAYABLE = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


class _RetryDroppedConnection(httpx.HTTPTransport):
    """
    Retries a request whose connection died before the response came back.

    postgrest-py builds its session with http2=True. Supabase's edge answers a
    couple of requests on such a connection and then closes it with a graceful
    GOAWAY — NO_ERROR, last_stream_id set — which says the streams above that id
    were never processed and the client should replay them elsewhere. httpx does
    not replay them; it raises RemoteProtocolError, and the request fails.

    Every signed-in endpoint issues two or more queries in a row through one
    client, so the second or third of them landed on exactly that closed
    connection. /api/me and /api/insights returned 500 on essentially every
    dashboard load, which reached the browser as "לא הצלחנו לטעון את הנתונים".

    The clients below therefore speak HTTP/1.1, where this failure mode does not
    exist. This transport covers what remains: any connection that dies mid-flight
    — an idle keep-alive socket reaped at the far end, a network blip on the long
    hop between the app's region and the database's — is retried once on a fresh
    connection instead of becoming a 500.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return super().handle_request(request)
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError):
            if request.method not in _REPLAYABLE:
                raise
            return super().handle_request(request)


def _http_client() -> httpx.Client:
    """
    A fresh httpx client per Supabase client, never a shared one.

    `create_client(...).postgrest.auth(token)` writes the caller's JWT onto this
    object's headers. One client shared between requests would therefore carry
    one user's token into another user's query — the precise leak that RLS exists
    to prevent, arriving through the back door.
    """
    return httpx.Client(
        http2=False,
        timeout=httpx.Timeout(30.0, connect=10.0),
        transport=_RetryDroppedConnection(retries=2),
    )


def get_user_client(access_token: str) -> Client:
    """
    Client scoped to the signed-in user. Every request carries the user's own
    JWT to Supabase, so Row Level Security enforces access at the database —
    not app code deciding who gets which rows.
    """
    client = create_client(
        SUPABASE_URL,
        SUPABASE_PUBLISHABLE_KEY,
        SyncClientOptions(httpx_client=_http_client()),
    )
    client.postgrest.auth(access_token)
    return client


def get_anon_client() -> Client:
    """
    Client with no user attached. Only for the auth calls that happen before
    anyone is signed in — sign-in and sign-up. It carries the publishable key,
    which grants nothing on its own, so a data read through it returns nothing
    but the empty set RLS allows an anonymous caller.
    """
    return create_client(
        SUPABASE_URL,
        SUPABASE_PUBLISHABLE_KEY,
        SyncClientOptions(httpx_client=_http_client()),
    )


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
    return create_client(
        SUPABASE_URL,
        SUPABASE_SECRET_KEY,
        SyncClientOptions(httpx_client=_http_client()),
    )

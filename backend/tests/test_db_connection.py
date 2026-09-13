"""
The Supabase connection's failure behaviour.

Written after every signed-in page returned 500. postgrest-py opens its session
with http2=True; Supabase's edge served two requests on that connection and then
closed it with a graceful GOAWAY, and httpx raised RemoteProtocolError rather
than replaying the streams the GOAWAY said were never processed. Every endpoint
behind a login issues two or more queries through one client, so the failure was
not intermittent — it was most of the product.

Nothing here touches the network. These assert the three properties that made
the failure possible, so a future change that reintroduces any of them fails
here instead of in front of a user.
"""
import httpx
import pytest

from app.db import _REPLAYABLE, _http_client, _RetryDroppedConnection


def test_the_connection_does_not_speak_http2():
    """
    The specific fix. HTTP/1.1 has no GOAWAY, so the failure mode cannot recur.

    Read off the connection pool rather than from a request, so the assertion
    holds without a network: the pool is where httpx records which protocols it
    is willing to negotiate.
    """
    client = _http_client()
    try:
        assert client._transport is not None
        # httpx exposes the h2 decision only through the transport's pool.
        pool = client._transport._pool
        assert pool._http2 is False, "postgrest's http2=True must not come back"
        assert pool._http1 is True
    finally:
        client.close()


def test_each_client_is_its_own_object():
    """
    A shared httpx client would be a cross-user token leak, not an optimisation.

    `get_user_client` writes the caller's JWT onto the client's headers, so two
    requests sharing one object would mean one user's token on another user's
    query — RLS bypassed through the back door rather than through the database.
    """
    a, b = _http_client(), _http_client()
    try:
        assert a is not b
    finally:
        a.close()
        b.close()


@pytest.fixture
def attempts(monkeypatch):
    """
    Counts how many times the request reaches the socket layer.

    Patched at httpx.HTTPTransport — the base class our transport delegates to —
    rather than on the subclass, because the subclass's handle_request *is* the
    retry being tested. Replacing it would assert on the stub instead of on the
    code.
    """
    class Log:
        def __init__(self):
            self.methods: list[str] = []
            self.failures = 1

        def __len__(self):
            return len(self.methods)

        def set_failures(self, n):
            self.failures = n

    log = Log()

    def record(self, request):
        log.methods.append(request.method)
        if len(log) <= log.failures:
            raise httpx.RemoteProtocolError("connection terminated", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record)
    return log


@pytest.mark.parametrize("method", sorted(_REPLAYABLE))
def test_a_dropped_read_is_retried_rather_than_failing(method, attempts):
    with httpx.Client(transport=_RetryDroppedConnection()) as client:
        response = client.request(method, "https://example.supabase.co/rest/v1/companies")
    assert response.status_code == 200
    assert len(attempts) == 2, "the dropped request should have been replayed once"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_a_dropped_write_is_not_replayed(method, attempts):
    """
    A dropped connection says the response never arrived. It does not say the
    server did nothing — so replaying an upload could store it twice. These fail
    loudly instead, which is the honest outcome.
    """
    with httpx.Client(transport=_RetryDroppedConnection()) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            client.request(method, "https://example.supabase.co/rest/v1/uploads")
    assert len(attempts) == 1


def test_a_retry_that_also_fails_surfaces_the_error(attempts):
    """Two failures in a row is a real outage, and must not be swallowed."""
    attempts.set_failures(2)
    with httpx.Client(transport=_RetryDroppedConnection()) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            client.get("https://example.supabase.co/rest/v1/companies")
    assert len(attempts) == 2

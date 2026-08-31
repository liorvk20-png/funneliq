"""
Route wiring. The static mount sits at "/" and claims every unmatched path, so
a route declared after it silently disappears — these fail loudly if that ever
happens again.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

GATED = ["/api/insights", "/api/funnel-records/sample", "/api/predict/1", "/api/me"]


def test_health_is_public():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_root_serves_the_dashboard():
    r = client.get("/")
    assert r.status_code == 200
    assert "FunnelIQ" in r.text


@pytest.mark.parametrize("path", GATED)
def test_gated_without_a_token(path):
    """Must be 401, not FastAPI's default 422 for a missing required header."""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", GATED)
def test_gated_with_a_bad_token(path):
    r = client.get(path, headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 401


def test_config_never_exposes_the_secret_key():
    body = client.get("/api/config").text
    assert "sb_secret" not in body
    assert set(client.get("/api/config").json()) == {"supabaseUrl", "publishableKey"}


def test_the_static_mount_has_not_swallowed_the_api():
    for path in ["/health", "/api/config"]:
        assert client.get(path).headers["content-type"].startswith("application/json")


# --------------------------------------------------------------- interface
# The dashboard is one static file, so nothing type-checks it. These are the
# cheapest checks that would have caught the two ways it has actually broken:
# a route wired in the sidebar with no page behind it, and an element the
# JavaScript looks up by id that no longer exists in the markup.
PAGE_KEYS = ["home", "data", "predict", "funnel", "budget", "upload", "profile"]


@pytest.fixture(scope="module")
def dashboard() -> str:
    return client.get("/").text


def test_dashboard_is_hebrew_and_right_to_left(dashboard):
    assert 'lang="he"' in dashboard and 'dir="rtl"' in dashboard


@pytest.mark.parametrize("page", PAGE_KEYS)
def test_every_sidebar_link_has_a_page_behind_it(dashboard, page):
    """A link to a route with no PAGES entry silently falls back to home."""
    assert f'data-page="{page}"' in dashboard
    assert f"PAGES.{page} =" in dashboard
    assert f'"{page}"' in dashboard.split("const ROUTE_ORDER")[1].split("\n")[0]


@pytest.mark.parametrize("element", [
    "loginView", "appView", "loginForm", "tabSignIn", "tabSignUp", "companyField",
    "company", "email", "password", "loginBtn", "loginErr", "loginOk",
    "tagline", "nav", "who", "logoutBtn", "pageBody", "main",
    "pwToggle", "emailLabel", "emailHint",
])


def test_elements_the_script_looks_up_exist(dashboard, element):
    assert f'id="{element}"' in dashboard


def test_the_password_can_be_revealed(dashboard):
    """
    A person typing a password they cannot see on a phone keyboard gets it
    wrong and is told their credentials are invalid, with no way to tell which
    part was wrong. The toggle has to carry its state for a screen reader too.
    """
    assert 'id="pwToggle"' in dashboard and 'aria-pressed' in dashboard


def test_signing_in_accepts_a_company_name(dashboard):
    """
    type="email" on the sign-in field would reject a company name in the
    browser, before the request that knows how to resolve it is ever made.
    """
    assert 'אימייל או שם חברה' in dashboard
    assert '$("email").type = up ? "email" : "text"' in dashboard


def test_the_browser_no_longer_talks_to_supabase_auth_directly(dashboard):
    """
    Both flows go through our own endpoints so every refusal is translated in
    one place. A call left pointing at Supabase would answer in English again.
    """
    assert "/auth/v1/token" not in dashboard
    assert "/auth/v1/signup" not in dashboard
    assert '"/api/login"' in dashboard and '"/api/signup"' in dashboard

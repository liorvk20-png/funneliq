"""
Route wiring. The static mount sits at "/" and claims every unmatched path, so
a route declared after it silently disappears — these fail loudly if that ever
happens again.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

GATED = ["/api/insights", "/api/funnel-records/sample", "/api/predict/1",
         "/api/me", "/api/models", "/api/analysis"]


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
def test_gated_with_a_bad_token(path, unreachable_jwks_stubbed):
    """
    A token that does not verify is a 401.

    This test used to pass without ever verifying anything. SUPABASE_URL in the
    test environment points at a domain that does not exist, so the JWKS fetch
    failed and the failure was caught by the same clause as an invalid token —
    the assertion below held for a reason that had nothing to do with tokens.
    Stubbing the fetch is what makes it a test of the thing it names.
    """
    r = client.get(path, headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", GATED)
def test_a_key_server_we_cannot_reach_does_not_sign_anyone_out(path, monkeypatch):
    """
    The key cache is empty on a freshly started container, so the first
    authenticated request after every deploy fetches over the network. A blip
    there is our problem, not a bad token: answering 401 would clear the
    person's session and tell them it had expired.
    """
    import jwt

    from app import auth

    def boom(_token):
        raise jwt.exceptions.PyJWKClientConnectionError("cannot reach the key server")

    monkeypatch.setattr(auth._jwk_client, "get_signing_key_from_jwt", boom)
    r = client.get(path, headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 503
    assert "רגע" in r.json()["detail"]


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
    # the chrome: theme, drawer, identity, messages
    "themeBtn", "navToggle", "scrim", "sideNav", "brandSlot", "avatar",
    "whoCompany", "toasts",
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


# ------------------------------------------------------- the column mapping
# The answers to the column questions arrive as a JSON form field. Whatever is
# in it renames columns, so it is checked rather than trusted — ingest verifies
# every value against the file, and these cover the shape of the field itself.
@pytest.mark.parametrize("raw", ["not json at all", "[1, 2, 3]", '"a string"'])
def test_a_malformed_column_mapping_is_refused(raw, unreachable_jwks_stubbed):
    r = client.post("/api/uploads/preview", data={"mapping": raw},
                    files={"file": ("f.csv", b"a,b\n1,2\n")})
    # 401 first: the token check runs before the body is looked at, which is
    # the order that keeps an unauthenticated caller from reaching the parser.
    assert r.status_code == 401


# ------------------------------------------------------------- company logo
# The one place in this product where a file one person uploads is rendered in
# another person's browser, which is what makes the format list a security
# boundary rather than a convenience.
from app.main import _check_logo  # noqa: E402

PNG_PIXEL = ("data:image/png;base64,"
             "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_a_real_png_is_accepted():
    assert _check_logo(PNG_PIXEL) == PNG_PIXEL


def test_clearing_the_logo_is_allowed():
    assert _check_logo(None) is None and _check_logo("") is None


def test_an_svg_is_refused():
    """
    An SVG is a document that can carry script. Every other format here is
    inert, and the difference matters because this file is chosen by a customer
    and shown to their colleagues.
    """
    with pytest.raises(HTTPException) as exc:
        _check_logo("data:image/svg+xml;base64,PHN2Zy8+")
    assert "SVG" in exc.value.detail


@pytest.mark.parametrize("value", [
    "https://example.com/logo.png",          # not a data URL at all
    "data:text/html;base64,PGgxPmhpPC9oMT4",  # a document wearing an image's clothes
    "data:image/png,notbase64",               # missing the base64 marker
    "data:image/png;base64,!!!not base64!!!",  # header lies about the payload
])
def test_anything_that_is_not_an_inert_image_is_refused(value):
    with pytest.raises(HTTPException):
        _check_logo(value)


def test_an_oversized_logo_is_refused():
    with pytest.raises(HTTPException) as exc:
        _check_logo("data:image/png;base64," + "A" * 500_000)
    assert exc.value.status_code == 413


def test_api_me_never_names_a_column_that_a_migration_might_not_have_added():
    """
    /api/me runs on every sign-in, and PostgREST rejects the whole query if one
    named column is missing. Naming logo_b64 there took the entire product down
    between a deploy and its migration — every user, on every page, until
    someone ran the SQL. Asking for the whole row instead makes the two orders
    of operations equivalent.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    me = source.split('@app.get("/api/me")')[1].split("@app.")[0]
    assert 'client.table("companies").select("*")' in me
    # The comment above that line explains the rule by naming the column, so
    # only the code is searched — the same trap the loader test fell into.
    code = "\n".join(line for line in me.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "logo_b64" not in code


# ------------------------------------------------------------- the chrome
def test_no_native_dialogs_remain(dashboard):
    """
    alert() and confirm() block the page, cannot be styled, cannot be written in
    Hebrew by us, and are prefixed by the browser with the host name. A product
    being sold should have none of them.
    """
    import re
    # The last script block is the application; the first is the four-line
    # theme bootstrap in the head. Comments are stripped for real rather than
    # line by line, because the comment explaining why alert() is gone would
    # otherwise be read as a use of it.
    script = dashboard.rsplit("<script>", 1)[1]
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    code = re.sub(r"(?m)//.*$", "", code)
    assert not re.search(r"(?<![.\w])alert\s*\(", code)
    assert not re.search(r"(?<![.\w])confirm\s*\(", code)


def test_both_themes_define_every_role_token(dashboard):
    """
    A token defined only in the light block renders as nothing in dark mode —
    invisible text on an invisible background, and no error anywhere.
    """
    css = dashboard.split("<style>")[1].split("</style>")[0]
    light = css.split(":root{")[1].split("}")[0]
    dark = css.split(':root[data-theme="dark"]{')[1].split("}")[0]
    roles = ["--bg", "--surface", "--surface-2", "--surface-3", "--line",
             "--line-strong", "--text", "--text-2", "--text-3",
             "--brand", "--brand-ink", "--brand-soft", "--on-brand"]
    for token in roles:
        assert f"{token}:" in light, f"{token} missing from the light theme"
        assert f"{token}:" in dark, f"{token} missing from the dark theme"


def test_the_page_declares_what_it_needs_to_be_installable(dashboard):
    for tag in ['rel="manifest"', 'rel="icon"', 'name="description"',
                'name="theme-color"', 'name="viewport"']:
        assert tag in dashboard


@pytest.mark.parametrize("path,kind", [
    ("/favicon.svg", "image/svg+xml"),
    ("/manifest.webmanifest", "manifest"),
])
def test_the_assets_the_page_links_to_are_actually_served(path, kind):
    """A linked file that 404s is invisible in development and obvious in a tab."""
    r = client.get(path)
    assert r.status_code == 200 and kind in r.headers["content-type"]


def test_motion_and_contrast_preferences_are_respected(dashboard):
    css = dashboard.split("<style>")[1].split("</style>")[0]
    assert "prefers-reduced-motion" in css
    assert "prefers-color-scheme" in css
    assert ":focus-visible" in css


def test_the_two_dark_blocks_stay_identical(dashboard):
    """
    The dark palette is written twice — once for the explicit toggle, once for
    a browser that asks for dark before any script runs. Plain CSS cannot share
    a token block between two selectors, so the only thing keeping them from
    drifting is this.
    """
    import re
    css = dashboard.split("<style>")[1].split("</style>")[0]
    explicit = re.search(r':root\[data-theme="dark"\]\{\n(.*?)\n\}', css, re.S).group(1)
    media = re.search(r':root:not\(\[data-theme="light"\]\)\{\n(.*?)\n  \}', css, re.S).group(1)
    normal = lambda block: sorted(  # noqa: E731
        line.strip() for line in block.splitlines() if ":" in line)
    assert normal(explicit) == normal(media)


def test_the_theme_is_set_before_the_page_paints(dashboard):
    """
    Deciding the theme in the main script, which runs at the end of the body,
    shows every dark-mode user a white flash on every load.
    """
    head = dashboard.split("</head>")[0]
    assert "data-theme" in head and "prefers-color-scheme" in head

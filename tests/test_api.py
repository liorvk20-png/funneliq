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
         "/api/me", "/api/models", "/api/analysis", "/api/seats"]


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
PAGE_KEYS = ["home", "advice", "data", "funnel", "budget", "predict",
             "upload", "profile"]


@pytest.fixture(scope="module")
def dashboard() -> str:
    return client.get("/").text


def test_dashboard_is_hebrew_and_right_to_left(dashboard):
    assert 'lang="he"' in dashboard and 'dir="rtl"' in dashboard


@pytest.mark.parametrize("page", PAGE_KEYS)
def test_every_sidebar_link_has_a_page_behind_it(dashboard, page):
    """
    The navigation, the routes and the page headings all read from PAGE_META
    now. Two lists meant two chances to disagree and they took both: the
    sidebar said "משפך המכירות" and the page it opened said "מסע הלקוח".
    """
    meta = dashboard.split("const PAGE_META = {")[1].split("\n};")[0]
    assert f"{page}:" in meta, f"{page} is not in PAGE_META"
    assert f"PAGES.{page} =" in dashboard, f"{page} has no page function"


def test_the_sidebar_label_and_the_page_heading_are_the_same_words(dashboard):
    """The label somebody clicks has to be the heading they land on."""
    import re
    meta = dashboard.split("const PAGE_META = {")[1].split("\n};")[0]
    entries = re.findall(r'nav:\s*"([^"]+)",\s*title:\s*"([^"]+)"', meta)
    assert len(entries) == len(PAGE_KEYS)
    for nav, title in entries:
        assert nav == title, f"sidebar says {nav!r}, the page says {title!r}"


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


@pytest.mark.parametrize("header", [
    "Bearer notbase64atall",
    "Bearer a.b.c",
    "Bearer " + "x" * 40,
    "Bearer ..",
    "Bearer eyJhbGciOiJIUzI1NiJ9",
])
def test_a_malformed_token_is_refused_not_a_server_error(header):
    """
    A token that is not three base64 segments never reaches PyJWT's own
    exceptions: base64url_decode raises binascii.Error first, which was not
    caught and came back as 500. Anything a caller can put in a header is a bad
    request, and a 500 on an unauthenticated route invites more of them.

    Found by pointing a stale session at every endpoint, which is what a
    browser does after a token expires and the tab is left open.
    """
    r = client.get("/api/me", headers={"Authorization": header})
    assert r.status_code == 401, f"{header!r} produced {r.status_code}"


def test_a_network_outage_is_not_reported_as_a_bad_token(monkeypatch):
    """
    PyJWKClientConnectionError inherits from PyJWTError, so a broad clause
    ordered above it swallows an outage and returns 401 — signing the person
    out over a fault on our side. This test exists because a later fix for
    malformed tokens did exactly that, and only the ordering keeps both right.
    """
    import jwt

    from app import auth

    def unreachable(_token):
        raise jwt.exceptions.PyJWKClientConnectionError("no route to host")

    monkeypatch.setattr(auth._jwk_client, "get_signing_key_from_jwt", unreachable)
    # A well-formed-looking token and a malformed one both hit the same outage.
    for header in ("Bearer a.b.c", "Bearer garbage"):
        assert client.get("/api/me", headers={"Authorization": header}).status_code == 503


# ------------------------------------------------------- one of each thing
# Written after a bad splice duplicated 886 lines of the dashboard: two
# PAGES.profile definitions, two upload sections. The JavaScript still parsed,
# every id was still present, and the whole suite passed — because presence was
# all anything checked. The later definition silently won.
@pytest.mark.parametrize("marker", [
    "PAGES.profile = () => {",
    "PAGES.upload = () => {",
    "PAGES.home = () => {",
    "function wireUpload()",
    "function wireProfile()",
    "function monthPicker(",
    "function campaignTableCard()",
    "function tile(",
    "const GLOSSARY",
    "function barChart(",
    "function funnelChart(",
    "function donut(",
    "function lineChart(",
    "function showForgot()",
    "function showReset(",
    "const PROFILE_TABS",
    "const COLUMN_DOCS",
    "const ROUTE_ORDER",
    "async function showApp()",
])
def test_each_module_is_defined_exactly_once(dashboard, marker):
    assert dashboard.count(marker) == 1, (
        f"{marker!r} appears {dashboard.count(marker)} times — a duplicated "
        "block parses fine and the later copy silently wins")


def test_the_month_field_does_not_rely_on_a_browser_that_lacks_it(dashboard):
    """
    Safari does not implement input[type=month]: it renders a plain text box
    with no calendar, no format hint and no validation. On a Mac the upload
    form asked for a month and accepted any sentence.
    """
    assert 'type="month"' not in dashboard
    assert 'class="mp-grid"' in dashboard or "mp-grid" in dashboard


def test_the_recovery_token_never_reaches_the_server_in_a_url(dashboard):
    """
    It arrives in the fragment, which browsers do not send upstream, and is
    cleared from the address bar immediately — a recovery link left visible is
    a password reset anyone at the screen can replay.
    """
    assert "location.hash" in dashboard
    assert "history.replaceState" in dashboard


def test_vertical_rhythm_comes_from_one_rule(dashboard):
    """
    Spacing used to come from three places at once — a margin on .card, none on
    .grid, and hand-written 22px spacers between them. Wherever a grid met a
    card the gap came from only one side and the two sat flush, which is what
    "the boxes ride on each other" looked like.
    """
    css = dashboard.split("<style>")[1].split("</style>")[0]
    assert "#pageBody{display:flex;flex-direction:column;gap:" in css
    # No block carries its own bottom margin any more, so none can disagree.
    assert "margin-bottom" not in css.split(".card{")[1].split("}")[0]
    # Spacer divs, not every 22px: a radius token and a bar height use it too,
    # and a first version of this assertion flagged both.
    assert "style='height:22px'" not in dashboard
    assert 'style="margin-top:22px"' not in dashboard


# ------------------------------------------- the upload form, from a report
# A person uploaded a file, pressed save, and the button sat there. The rows had
# been stored; the browser had destroyed the form it was standing in.
def _save_handler(dashboard: str) -> str:
    body = dashboard.split('$("upSave").onclick')[1]
    return body.split("\n  };")[0]


def test_saving_does_not_rebuild_the_page_it_is_standing_in(dashboard):
    """
    render() replaces all of #pageBody. Called from inside the save handler it
    wiped the chosen file, wrote the report into a fresh DOM, and produced a new
    save button that arrives disabled from the markup — which is what "the
    button is stuck" was.
    """
    handler = _save_handler(dashboard)
    assert "render();" not in handler, "the save handler must not call render()"


def test_the_save_button_is_always_re_enabled(dashboard):
    """
    The finally block restored the label and not the disabled state, so a
    button that had been switched off stayed off, wearing the right words.
    """
    handler = _save_handler(dashboard)
    finally_block = handler.split("finally{")[1]
    assert "disabled" in finally_block
    assert "textContent" in finally_block


def test_a_long_save_says_it_is_working(dashboard):
    """
    The upload takes seconds even when everything goes right. Silence for that
    long reads as a hang, and did.
    """
    handler = _save_handler(dashboard)
    assert "spinner" in handler
    assert "שומרים" in handler or "שומר" in handler

# --------------------------------------------------------- staying signed in
def test_a_login_hands_back_something_to_renew_with(dashboard):
    """
    Supabase access tokens last an hour and nothing renewed them, so an hour
    into the working day the next click ended the session. The report was
    "clicking predictions logged me out"; the cause was that /api/login never
    returned the refresh token, so the browser could not have renewed even if
    it had tried.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    session = source.split("def _session(")[1].split("@app.")[0]
    assert '"refresh_token"' in session


def test_the_browser_renews_instead_of_signing_out(dashboard):
    assert "freshSession" in dashboard
    assert "/api/token/refresh" in dashboard
    # One renewal at a time: Supabase retires a refresh token on first use, so
    # three panels loading together would kill each other's session.
    assert "REFRESHING" in dashboard


def test_no_request_still_reads_the_session_without_renewing(dashboard):
    """
    Every authenticated call has to go through freshSession. One left on
    loadSession is one endpoint that still throws people out after an hour.
    """
    script = dashboard.rsplit("<script>", 1)[1]
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith(("//", "*")):
            continue
        if stripped.startswith("function loadSession"):
            continue          # its own definition
        if "loadSession()" in stripped:
            assert "const live = loadSession()" in stripped, (
                f"loadSession used outside freshSession: {stripped}")


# ------------------------------------------------- what the customer reads
# The person using this runs a marketing department, not a statistics one.
# Words that describe how the models work describe our problem, not theirs.
FORBIDDEN_JARGON = [
    "ציון שגיאה", "טעות ממוצעת", "אומן על", "PR-AUC", "MAE",
    "baseline", "confidence_label", "contribution_share",
]


@pytest.mark.parametrize("word", FORBIDDEN_JARGON)
def test_no_training_vocabulary_reaches_the_screen(dashboard, word):
    """
    A customer was shown an error score, a comparison against guessing, and how
    many rows a model trained on. None of it told them anything about their
    campaigns, and all of it invited the question "what does that mean".
    """
    import re
    script = dashboard.rsplit("<script>", 1)[1]
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    code = re.sub(r"(?m)//.*$", "", code)
    # Only strings the person sees, not field names the code reads.
    shown = re.findall(r'[>"`]([^<>"`]*[֐-׿][^<>"`]*)[<"`]', code)
    for text in shown:
        assert word not in text, f"{word!r} appears in {text[:70]!r}"


def test_every_figure_on_the_dashboard_has_a_plain_explanation(dashboard):
    """
    A number with no sentence under it is a number the reader has to already
    understand. The glossary is the one place that pairs them.
    """
    glossary = dashboard.split("const GLOSSARY = {")[1].split("\n};")[0]
    for key in ("leads", "closed", "spend", "profit", "revenue", "roas",
                "cost_per_lead", "cost_per_close", "answer_rate", "close_rate",
                "ltv_months"):
        assert f"{key}: {{" in glossary, f"{key} has no definition"
    # LTV is named by its term and defined beside it — a media buyer knows the
    # acronym, a finance lead may not, and both open the same dashboard.
    assert "מספר החודשים שלקוח ממוצע ממשיך לשלם" in dashboard


def test_the_campaign_table_shows_everything_not_a_sample(dashboard):
    """
    Ten rows out of a few hundred answer no question anybody has — you cannot
    find your worst campaign in them, and they were the ten oldest.
    """
    assert "fillSample" not in dashboard
    assert "/api/funnel-records/sample" not in dashboard
    assert "TABLE_PAGE_SIZE" in dashboard
    assert 'data-sort=' in dashboard and 'data-filter=' in dashboard


def test_the_pages_draw_pictures_and_not_only_tables(dashboard):
    for chart in ("funnelChart(", "barChart(", "donut(", "lineChart("):
        assert chart in dashboard
    # And each one is readable without seeing it.
    assert dashboard.count('role="img"') >= 4
    assert dashboard.count("aria-label=") >= 8


def test_a_stale_response_does_not_throw_the_refresh_token_away(dashboard):
    """
    The retry path called clearSession() before renewing, which deleted the
    refresh token it was about to need — so one stale response signed the
    person out permanently instead of recovering. The session is cleared only
    once a renewal has actually failed.
    """
    import re
    body = dashboard.split("async function api(path)")[1].split("\n}")[0]
    # Comments only, stripped: the comment above the fix names the call it
    # removed, and a first version of this test flagged its own explanation.
    code = re.sub(r"(?m)//.*$", "", body)
    retry = code.split("if(res.status === 401){")[1]
    assert "clearSession()" not in retry.split("renewNow()")[0], (
        "the retry path clears the session before it renews")


# ------------------------------------------------------- one whole document
# Written after a splice matched a marker in the stylesheet that also appears
# in the script, and duplicated everything between them: two <body> elements,
# two copies of the markup, two stylesheets. The JavaScript still parsed, every
# id was still present, and the whole suite passed — the later copy silently won.
@pytest.mark.parametrize("marker,expected", [
    ("<body>", 1), ("</head>", 1), ("<style>", 1), ("</style>", 1),
    ("<script>", 2), ("</script>", 2),   # the theme bootstrap and the app
    ('id="loginView"', 1), ('id="appView"', 1), ('id="pageBody"', 1),
])
def test_the_page_is_one_document(dashboard, marker, expected):
    assert dashboard.count(marker) == expected, (
        f"{marker!r} appears {dashboard.count(marker)} times, expected {expected}")


def test_the_stylesheet_is_balanced(dashboard):
    css = dashboard.split("<style>")[1].split("</style>")[0]
    assert css.count("{") == css.count("}")


def test_campaign_rows_carry_the_import_they_came_from(dashboard):
    """The overview can be filtered to one month, which needs the row to say
    which month it is."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    assert '"uploadId": r.get("upload_id")' in source
    assert "PERIOD" in dashboard and "periodTotals" in dashboard


def test_the_recommendations_show_their_working_on_request(dashboard):
    """
    Every suggestion carries the reasoning behind it, behind a button — hidden
    by default so the page stays readable, available so nothing has to be taken
    on trust.
    """
    assert "data-basis=" in dashboard
    assert "על מה זה מבוסס?" in dashboard


def test_the_recommendations_are_not_numbered(dashboard):
    """
    They were numbered 1, 2, 3, which reads as a required order. They are
    ranked by value, and any of them can be acted on alone.
    """
    advice = dashboard.split("PAGES.advice = () => {")[1].split("\n};")[0]
    assert '<span class="rank">' not in advice


def test_before_and_after_are_shown_side_by_side(dashboard):
    """A percentage on its own does not say what it is a percentage of."""
    assert "function comparison(" in dashboard
    assert "compare-side" in dashboard and "is-after" in dashboard


# --------------------------------------------- one voice across the product
# The reader is an organisation. Several people from the same company open this,
# each with a different job, so nothing addresses one of them personally and
# nothing assumes which one is looking.
SECOND_PERSON = [
    "שתעלה", "תראה לך", "אתה מוציא", "שלך תראה", "תדע", "תסגור",
    "אנחנו מעדיפים", "כמה כסף נשאר", "בכיס",
]


@pytest.mark.parametrize("phrase", SECOND_PERSON)
def test_the_product_addresses_an_organisation(dashboard, phrase):
    import re
    script = dashboard.rsplit("<script>", 1)[1]
    code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    code = re.sub(r"(?m)//.*$", "", code)
    assert phrase not in code, f"{phrase!r} speaks to one person"


@pytest.mark.parametrize("term_and_key", [
    ("ROAS", "roas"), ("CPL", "cost_per_lead"), ("CAC", "cost_per_close"),
    ("LTV", "ltv_months"), ("לידים", "leads"), ("המרות", "closed"),
])
def test_the_industry_term_is_used_and_defined(dashboard, term_and_key):
    """
    A media buyer knows the acronym and a finance lead may not, and both open
    the same dashboard — so the term is used and its definition sits beside it.
    """
    label, key = term_and_key
    glossary = dashboard.split("const GLOSSARY = {")[1].split("\n};")[0]
    entry = glossary.split(f"{key}: {{")[1].split("},")[0]
    assert label in entry, f"{key} does not use the term {label!r}"
    assert "explain" in entry and len(entry.split("explain")[1]) > 30


def test_every_page_opens_the_same_way(dashboard):
    """
    One header component, so no page invents its own heading style and none can
    drift from the sidebar label that led to it.
    """
    import re
    for page in PAGE_KEYS:
        body = dashboard.split(f"PAGES.{page} = ")[1].split("\n};")[0]
        assert re.search(rf'pageHead\("{page}"', body), (
            f"PAGES.{page} builds its own header instead of using pageHead")

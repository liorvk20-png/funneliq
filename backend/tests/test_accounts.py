"""
Sign-in and sign-up, without a network.

The endpoints themselves are exercised against real Supabase by hand; what runs
here is the logic that decides what the person is told and which account a
company name resolves to. Both are reachable without any credential, so they
are worth testing on every commit rather than only when someone remembers to
run the manual script.
"""
import pytest
from fastapi.testclient import TestClient

from app.accounts import MESSAGES, Ambiguous, email_for_company, looks_like_email, translate
from app.main import app

client = TestClient(app)


# ------------------------------------------------------------- translation
@pytest.mark.parametrize("english", [
    "Invalid login credentials",
    "Email not confirmed",
    "User already registered",
    "Password should be at least 6 characters",
    "For security purposes, you can only request this after 45 seconds",
])
def test_the_errors_a_person_actually_sees_are_hebrew(english):
    """The interface is Hebrew; Supabase answers in English. These are the ones
    a person hits often enough that leaving them untranslated is a defect."""
    hebrew = translate(english)
    assert hebrew != english
    assert any("֐" <= ch <= "ת" for ch in hebrew)


def test_an_unrecognised_error_is_passed_through_rather_than_swallowed():
    """
    A generic apology in place of an unknown error hides the one message that
    might explain a failure nobody anticipated. English is a worse experience
    than Hebrew and a far better one than silence.
    """
    assert translate("Something nobody planned for") == "Something nobody planned for"


def test_an_empty_error_still_says_something():
    assert translate("") and translate(None)


def test_the_needles_are_lowercase_or_they_never_match():
    """translate() lowercases its input, so an uppercase needle is dead code."""
    for needle, _ in MESSAGES:
        assert needle == needle.lower()


# ------------------------------------------------------- what was typed in
@pytest.mark.parametrize("typed,is_email", [
    ("someone@example.com", True),
    ("מאפיית הבוקר", False),
    ("Northbound Media", False),
    ("", False),
])
def test_an_address_is_told_apart_from_a_company_name(typed, is_email):
    assert looks_like_email(typed) is is_email


# --------------------------------------------------- company name -> account
class FakeTable:
    def __init__(self, rows):
        self.rows = rows
    def select(self, *_):
        return self
    def ilike(self, *_):
        return self
    def eq(self, *_):
        return self
    def execute(self):
        return type("R", (), {"data": self.rows})()


class FakeClient:
    """Just enough of the Supabase client for the lookup under test."""
    def __init__(self, companies, profiles):
        self.tables = {"companies": FakeTable(companies), "profiles": FakeTable(profiles)}
    def table(self, name):
        return self.tables[name]


def test_one_company_with_one_member_resolves():
    c = FakeClient([{"id": "c1"}], [{"email": "a@b.com"}])
    assert email_for_company(c, "Northbound") == "a@b.com"


def test_a_name_nobody_uses_resolves_to_nothing():
    """
    None, not an exception. The caller turns it into the same refusal a wrong
    password gets, so guessing company names cannot reveal which ones exist.
    """
    assert email_for_company(FakeClient([], []), "ghost") is None


def test_two_companies_sharing_a_name_is_refused_rather_than_guessed():
    """Picking one would sometimes sign a person into a workspace not theirs."""
    c = FakeClient([{"id": "c1"}, {"id": "c2"}], [{"email": "a@b.com"}])
    with pytest.raises(Ambiguous):
        email_for_company(c, "Media Group")


def test_a_company_with_two_members_is_refused_rather_than_guessed():
    """The schema already allows this; the name does not say which person."""
    c = FakeClient([{"id": "c1"}], [{"email": "a@b.com"}, {"email": "b@b.com"}])
    with pytest.raises(Ambiguous):
        email_for_company(c, "Northbound")


def test_a_company_with_no_usable_address_resolves_to_nothing():
    c = FakeClient([{"id": "c1"}], [{"email": None}])
    assert email_for_company(c, "Northbound") is None


# ----------------------------------------------------------- the endpoints
@pytest.mark.parametrize("path,body", [
    ("/api/login", {"identifier": "", "password": ""}),
    ("/api/login", {"identifier": "a@b.com", "password": ""}),
    ("/api/signup", {"email": "a@b.com", "password": "secret123", "company": ""}),
    ("/api/signup", {"email": "", "password": "secret123", "company": "x"}),
])
def test_missing_fields_are_refused_before_supabase_is_called(path, body):
    r = client.post(path, json=body)
    assert r.status_code == 400
    assert any("֐" <= ch <= "ת" for ch in r.json()["detail"])


def test_a_very_long_company_name_is_refused():
    r = client.post("/api/signup",
                    json={"email": "a@b.com", "password": "secret123", "company": "x" * 121})
    assert r.status_code == 400


def test_login_and_signup_need_no_token():
    """They run before anyone has one; requiring auth here would be a deadlock."""
    for path in ("/api/login", "/api/signup"):
        assert client.post(path, json={}).status_code == 400


# ------------------------------------------------------ password recovery
def test_a_reset_request_never_reveals_whether_an_account_exists():
    """
    The same answer either way. Anything else turns this endpoint into a
    membership check: type an address, read the response, learn whether that
    person uses the product.
    """
    unknown = client.post("/api/password/forgot",
                          json={"email": "nobody-at-all@funneliq.test"})
    assert unknown.status_code == 200 and unknown.json() == {"sent": True}


def test_a_malformed_address_is_refused_before_anything_is_sent():
    r = client.post("/api/password/forgot", json={"email": "not-an-email"})
    assert r.status_code == 400


@pytest.mark.parametrize("body,reason", [
    ({"accessToken": "", "password": "abcdef"}, "no token"),
    ({"accessToken": "x.y.z", "password": "12345"}, "password too short"),
    ({"password": "abcdef"}, "token missing entirely"),
])
def test_a_reset_without_what_it_needs_is_refused(body, reason):
    r = client.post("/api/password/reset", json=body)
    assert r.status_code == 400, reason
    assert any("֐" <= ch <= "ת" for ch in r.json()["detail"])


@pytest.mark.parametrize("english", [
    "Invalid JWT structure",
    "JWT expired",
    "New password should be different from the old password.",
])
def test_recovery_errors_reach_the_person_in_hebrew(english):
    """These are the three a person actually meets on a reset link."""
    assert any("֐" <= ch <= "ת" for ch in translate(english))


# ------------------------------------------------------------------ seats
def test_signing_up_needs_a_company_or_an_invitation():
    """
    Two ways in, and they need different fields. Someone joining a workspace
    has a code, not a company name — asking them to invent one would create a
    second workspace with a single member, which is what the invitation exists
    to prevent.
    """
    complete = {"email": "a@b.com", "password": "Secret123!",
                "passwordConfirm": "Secret123!", "fullName": "דנה כהן"}
    r = client.post("/api/signup", json=complete)
    assert r.status_code == 400
    assert "חברה" in r.json()["detail"]


@pytest.mark.parametrize("body", [
    {"email": "not-an-email", "role": "viewer"},
    {"email": "a@b.com", "role": "superuser"},
    {},
])
def test_a_bad_invitation_is_refused(body, unreachable_jwks_stubbed):
    """The token check runs first, which is the order that keeps an
    unauthenticated caller away from the parser."""
    assert client.post("/api/seats", json=body).status_code == 401


# ------------------------------------------------------- password strength
@pytest.mark.parametrize("password,missing", [
    ("Short1!", "8 תווים"),
    ("alllowercase1!", "אות גדולה"),
    ("ALLUPPERCASE1!", "אות קטנה"),
    ("NoDigitsHere!", "ספרה"),
    ("NoSymbol1here", "סימן"),
])
def test_a_weak_password_is_refused_and_says_what_is_missing(password, missing):
    """
    Naming the rule that failed is the difference between a person fixing it
    and a person guessing. All five are also shown as they type, so nobody
    meets them for the first time in an error.
    """
    r = client.post("/api/signup", json={
        "email": "a@b.com", "password": password, "passwordConfirm": password,
        "fullName": "דנה כהן", "company": "חברה"})
    assert r.status_code == 400
    assert missing in r.json()["detail"]


def test_the_two_password_fields_have_to_agree():
    r = client.post("/api/signup", json={
        "email": "a@b.com", "password": "Secret123!", "passwordConfirm": "Secret124!",
        "fullName": "דנה כהן", "company": "חברה"})
    assert r.status_code == 400 and "זהות" in r.json()["detail"]


def test_signing_up_needs_a_name():
    """The workspace lists people by name; an address is what a colleague
    already knows and the least useful thing for telling them apart."""
    r = client.post("/api/signup", json={
        "email": "a@b.com", "password": "Secret123!",
        "passwordConfirm": "Secret123!", "company": "חברה"})
    assert r.status_code == 400 and "שם מלא" in r.json()["detail"]


@pytest.mark.parametrize("field,value", [
    ("gender", "unicorn"),
    ("birthYear", "1066"),
    ("requestedRole", "superuser"),
])
def test_an_unknown_value_in_a_profile_field_is_refused(field, value):
    body = {"email": "a@b.com", "password": "Secret123!",
            "passwordConfirm": "Secret123!", "fullName": "דנה כהן",
            "company": "חברה", field: value}
    assert client.post("/api/signup", json=body).status_code == 400


def test_a_requested_role_is_a_request_and_not_a_grant():
    """
    A field the applicant fills in cannot decide their own access. Whoever
    opens a workspace administers it; whoever joins gets what their invitation
    carries.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    signup = source.split('@app.post("/api/signup")')[1].split("@app.")[0]
    assert '"requested_role"' in signup
    # never used to set a role directly
    assert 'role=' not in signup and '"role":' not in signup


# ------------------------------------------------ which member is asking
# /api/me returned whichever profile Postgres handed back first. RLS lets a
# colleague read every profile in their company, so with a second person in the
# workspace that was usually the admin's row — and an editor was shown the
# admin's name, role and controls. Everything worked while every company had
# exactly one member.
def _handler(name: str) -> str:
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    return source.split(name)[1].split("\n@app.")[0]


def test_api_me_returns_the_caller_and_not_a_colleague():
    body = _handler('@app.get("/api/me")')
    assert "user_id_for(token)" in body, (
        "/api/me does not filter profiles to the caller")


def test_the_role_check_reads_the_callers_own_row():
    """
    This one gates who may invite people. Reading somebody else's row let an
    editor open seats — RLS refused the write underneath, so nothing was
    created, but the product offered a permission it did not have.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    role = source.split("def _my_role(")[1].split("\ndef ")[0]
    assert "user_id_for(token)" in role
    assert 'r.get("user_id") == uid' in role


def test_every_read_of_profiles_is_either_filtered_or_company_wide():
    """
    Checks the assignment rather than the use, because a filter can sit on the
    line above. Two shapes are allowed: filtered to the caller, or asking only
    for company_id — which is the same for everyone in the workspace, so any
    row answers it.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    lines = source.splitlines()
    # A window rather than a regex: the assignment wraps over several lines and
    # a first attempt stopped at the first of them, cutting off the filter it
    # was looking for.
    for i, line in enumerate(lines):
        if not line.strip().startswith("profiles = "):
            continue
        statement = "\n".join(lines[i:i + 4])
        filtered = "user_id_for(token)" in statement
        company_only = 'select("company_id")' in statement
        assert filtered or company_only, (
            "profiles read without saying which member it is about:\n"
            + statement.strip()[:120])


@pytest.mark.parametrize("endpoint", [
    '@app.post("/api/uploads")',
    '@app.delete("/api/uploads/{upload_id}")',
    '@app.patch("/api/company")',
])
def test_a_viewer_is_refused_with_a_reason(endpoint):
    """
    The database refuses these for a viewer, correctly. Surfacing that as a 500
    told the person nothing they could act on; it is a 403 that names the
    permission and says who can change it.
    """
    body = _handler(endpoint)
    assert "HTTP_403_FORBIDDEN" in body
    assert "צפייה" in body

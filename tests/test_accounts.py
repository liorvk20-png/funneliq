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

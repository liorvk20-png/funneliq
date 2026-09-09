"""
Proves company isolation against the live database.

Creates two throwaway companies, gives each a row, and checks that neither can
see the other's — then deletes both. Run after applying 002_multi_tenancy.sql.

This is deliberately not a unit test: the thing being tested is a Postgres
policy, and only Postgres can answer whether it holds. A mock would test the
mock.

    python scripts/verify_isolation.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

URL = os.environ["SUPABASE_URL"]
PUBLISHABLE = os.environ["SUPABASE_PUBLISHABLE_KEY"]
SECRET = os.environ.get("SUPABASE_SECRET_KEY")
if not SECRET:
    sys.exit("SUPABASE_SECRET_KEY is needed to create and remove the test users.")

ADMIN = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}",
         "Content-Type": "application/json"}


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(
        f"{URL}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers or ADMIN)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def make_user(tag):
    email = f"isolation-{tag}-{uuid.uuid4().hex[:8]}@funneliq.test"
    password = uuid.uuid4().hex + "Aa1!"
    status, user = call("POST", "/auth/v1/admin/users",
                        {"email": email, "password": password, "email_confirm": True})
    assert status in (200, 201), f"could not create user: {status} {user}"
    status, tok = call("POST", "/auth/v1/token?grant_type=password",
                       {"email": email, "password": password},
                       {"apikey": PUBLISHABLE, "Content-Type": "application/json"})
    assert status == 200, f"could not sign in: {status} {tok}"
    return user["id"], tok["access_token"], email


def as_user(token):
    return {"apikey": PUBLISHABLE, "Authorization": f"Bearer {token}",
            "Content-Type": "application/json", "Prefer": "return=representation"}


ROW = {"ad_budget": 2500, "num_leads": 36, "leads_answered": 24,
       "leads_not_answered": 12, "followup_1": 19, "followup_2": 14,
       "followup_3": 11, "followup_4": 10, "followup_5": 7, "not_closed": 5,
       "closed": 2, "calls_to_closed": 2, "calls_to_not_closed": 4,
       "customer_acquisition_cost": 1250, "ltv_months": 38, "purchased": True,
       "upsell": False, "cumulative_profit": 20777, "referred": False}


def main() -> None:
    checks, failures = [], []

    def check(name, passed, detail=""):
        checks.append(name)
        print(f"  {'PASS' if passed else '*** FAIL ***'}  {name}{'  ' + detail if detail else ''}")
        if not passed:
            failures.append(name)

    print("creating two throwaway companies…")
    a_id, a_token, a_email = make_user("a")
    b_id, b_token, b_email = make_user("b")

    try:
        _, a_profile = call("GET", "/rest/v1/profiles?select=company_id", None, as_user(a_token))
        _, b_profile = call("GET", "/rest/v1/profiles?select=company_id", None, as_user(b_token))
        check("sign-up created a profile for A", bool(a_profile))
        check("sign-up created a profile for B", bool(b_profile))
        if not (a_profile and b_profile):
            sys.exit("\nThe sign-up trigger did not run. Apply 002_multi_tenancy.sql first.")

        a_company = a_profile[0]["company_id"]
        b_company = b_profile[0]["company_id"]
        check("the two companies are different", a_company != b_company)

        print("\ngiving each company one row…")
        st, _ = call("POST", "/rest/v1/funnel_records",
                          {**ROW, "company_id": a_company}, as_user(a_token))
        check("A can insert into its own company", st in (200, 201), f"({st})")
        st, _ = call("POST", "/rest/v1/funnel_records",
                     {**ROW, "company_id": b_company}, as_user(b_token))
        check("B can insert into its own company", st in (200, 201), f"({st})")

        print("\nthe checks that matter…")
        rows_url = "/rest/v1/funnel_records?select=id,company_id"
        _, a_sees = call("GET", rows_url, None, as_user(a_token))
        _, b_sees = call("GET", rows_url, None, as_user(b_token))
        check("A sees exactly its own row", len(a_sees or []) == 1 and
              all(r["company_id"] == a_company for r in a_sees), f"(saw {len(a_sees or [])})")
        check("B sees exactly its own row", len(b_sees or []) == 1 and
              all(r["company_id"] == b_company for r in b_sees), f"(saw {len(b_sees or [])})")

        # The hole a read-only policy would leave: writing INTO someone else.
        st, _ = call("POST", "/rest/v1/funnel_records",
                     {**ROW, "company_id": b_company}, as_user(a_token))
        check("A cannot insert into B's company", st not in (200, 201), f"({st})")

        # And the direct attempt to read another company by id.
        _, cross = call("GET", f"/rest/v1/funnel_records?company_id=eq.{b_company}",
                        None, as_user(a_token))
        check("A cannot read B's rows by asking for them", cross == [], f"(got {cross})")

        _, companies = call("GET", "/rest/v1/companies?select=id", None, as_user(a_token))
        check("A sees only its own company row", len(companies or []) == 1)

    finally:
        print("\ncleaning up…")
        for uid in (a_id, b_id):
            call("DELETE", f"/auth/v1/admin/users/{uid}")
        print(f"  removed {a_email} and {b_email}")

    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        sys.exit(1)
    print("Isolation holds.")


if __name__ == "__main__":
    main()

"""
Two companies, one analysis each, and no way to see the other's.

The findings tables are new and they hold the most quotable thing the product
produces: sentences about a company's performance. Everything else has been
isolated since 002; these four tables were added in 007 and their policies have
never been exercised against a real second tenant.

Creates two throwaway accounts, writes an analysis under each, and checks in
both directions. Cleans up whatever it created, including on failure.

    python scripts/verify_wp7_isolation.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db import get_user_client  # noqa: E402
from app.findings import repository  # noqa: E402
from app.findings.schema import Confidence, Direction, Finding, FindingType  # noqa: E402
from app.narrative.engine import Sentence  # noqa: E402

URL = os.environ["SUPABASE_URL"]
SECRET = os.environ["SUPABASE_SECRET_KEY"]
HEADERS = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}",
           "Content-Type": "application/json"}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        URL + path, method=method,
        data=json.dumps(body).encode() if body else None, headers=HEADERS)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def make_user(tag: str) -> tuple[str, str, str]:
    email = f"wp7-{tag}-{uuid.uuid4().hex[:8]}@funneliq.test"
    password = uuid.uuid4().hex + "Aa1!"
    _, user = call("POST", "/auth/v1/admin/users", {
        "email": email, "password": password, "email_confirm": True,
        "user_metadata": {"company_name": f"WP7 {tag}"}})
    _, token = call("POST", "/auth/v1/token?grant_type=password",
                    {"email": email, "password": password})
    return user["id"], token["access_token"], email


def sample(company_id: str) -> tuple[list[Finding], list[Sentence]]:
    finding = Finding(
        finding_type=FindingType.METRIC_CHANGE, metric_key="cost_per_lead",
        value_current=41.0, value_baseline=33.0, delta_abs=8.0, delta_pct=0.24,
        contribution_share=1.0, contribution_abs=8.0,
        denom_current=900, denom_baseline=880, significance_p=0.003,
        direction=Direction.UP, is_favorable=False, severity=78,
        confidence_label=Confidence.HIGH)
    sentence = Sentence(section="headline", ordinal=0, rule_key="headline_declined",
                        finding_id=str(finding.finding_id),
                        text_he=f"סוד של {company_id[:8]}")
    return [finding], [sentence]


def main() -> None:
    failures = 0

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal failures
        if not passed:
            failures += 1
        print(f"  {'PASS' if passed else 'FAIL'}  {name}{'  ' + detail if detail else ''}")

    a_id, a_token, a_email = make_user("a")
    b_id, b_token, b_email = make_user("b")
    try:
        a, b = get_user_client(a_token), get_user_client(b_token)
        a_company = a.table("profiles").select("company_id").execute().data[0]["company_id"]
        b_company = b.table("profiles").select("company_id").execute().data[0]["company_id"]
        check("the two accounts are in different companies", a_company != b_company)

        findings_a, sentences_a = sample(a_company)
        findings_b, sentences_b = sample(b_company)
        run_a = repository.save(a, a_company, findings_a, sentences_a)
        run_b = repository.save(b, b_company, findings_b, sentences_b)
        check("each company can write its own analysis", bool(run_a and run_b))

        own_a = repository.latest(a)
        check("a reads its own narrative", own_a["run"]["run_id"] == run_a)
        check("a's narrative is a's text",
              own_a["sentences"][0]["text_he"] == sentences_a[0].text_he)

        # The direct reads: every table, both directions.
        for table in ("analysis_runs", "findings", "narrative_outputs"):
            rows = b.table(table).select("*").execute().data
            foreign = [r for r in rows if r.get("company_id") == a_company]
            check(f"b sees nothing of a's {table}", not foreign,
                  f"({len(rows)} own rows)")

        seen = b.table("narrative_outputs").select("run_id").eq("run_id", run_a).execute().data
        check("b cannot fetch a's run by its id", not seen)

        # The write direction, which a read-only check would miss entirely.
        try:
            b.table("findings").insert({
                "finding_id": str(uuid.uuid4()), "company_id": a_company,
                "run_id": run_a, "finding_type": "metric_change",
                "metric_key": "cost_per_lead", "severity": 50,
                "confidence_label": "high"}).execute()
            check("b cannot file a finding under a", False, "the insert succeeded")
        except Exception:
            check("b cannot file a finding under a", True)

        try:
            b.table("narrative_outputs").insert({
                "company_id": a_company, "run_id": run_a, "section": "headline",
                "ordinal": 0, "rule_key": "x",
                "finding_id": str(findings_a[0].finding_id),
                "text_he": "טקסט מוזרק"}).execute()
            check("b cannot put words in a's report", False, "the insert succeeded")
        except Exception:
            check("b cannot put words in a's report", True)

        try:
            b.table("findings").delete().eq("run_id", run_a).execute()
            still = repository.latest(a)["findings"]
            check("b cannot delete a's findings", bool(still))
        except Exception:
            check("b cannot delete a's findings", True)
    finally:
        for user_id in (a_id, b_id):
            call("DELETE", f"/auth/v1/admin/users/{user_id}")
        service_cleanup()
        print(f"  cleaned up {a_email} and {b_email}")

    print(f"\n{'all checks passed' if not failures else f'{failures} CHECKS FAILED'}")
    sys.exit(1 if failures else 0)


def service_cleanup() -> None:
    """Companies survive their last member; the analysis rows cascade with them."""
    from app.db import get_service_client
    client = get_service_client()
    kept = {p["company_id"] for p in client.table("profiles").select("company_id").execute().data}
    for company in client.table("companies").select("id").execute().data:
        if company["id"] not in kept:
            client.table("companies").delete().eq("id", company["id"]).execute()


if __name__ == "__main__":
    main()

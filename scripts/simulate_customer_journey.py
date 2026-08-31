"""
A full dress rehearsal: two companies sign up, upload real campaign files
through the real endpoints, and every answer is checked against an independent
calculation rather than against a status code.

Status codes were never the problem on this project. Every bug that reached the
product returned 200: a thousand rows averaged as if they were three thousand,
a picker wired to ten campaigns, sums across the whole book labelled as one
month. So each check below recomputes the expected value with pandas and
compares, and prints the two side by side when they differ.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

API = os.environ.get("SIM_API", "http://127.0.0.1:8899")
URL, SECRET = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
ADMIN = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}",
         "Content-Type": "application/json"}

FAILURES: list[str] = []
CREATED: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    if not passed:
        FAILURES.append(f"{name}{'  — ' + detail if detail else ''}")
    print(f"  {'PASS' if passed else 'FAIL'}  {name}{'   ' + detail if detail else ''}")
    return passed


def close(name: str, got, want, tol: float = 0.05) -> bool:
    if got is None or want is None:
        return check(name, got == want, f"got {got!r} want {want!r}")
    ok = abs(float(got) - float(want)) <= tol
    return check(name, ok, f"got {got}  want {want}")


# ─────────────────────────────────────────────────────────── data
HEBREW_HEADERS = {
    "ad_budget": "תקציב", "num_leads": "לידים", "leads_answered": "ענו",
    "leads_not_answered": "לא ענו", "followup_1": "מעקב 1", "followup_2": "מעקב 2",
    "followup_3": "מעקב 3", "followup_4": "מעקב 4", "followup_5": "מעקב 5",
    "not_closed": "לא נסגרו", "closed": "נסגרו",
    "calls_to_closed": "שיחות עד סגירה", "calls_to_not_closed": "שיחות ללא סגירה",
    "customer_acquisition_cost": "עלות גיוס לקוח", "ltv_months": "שווי לקוח",
    "purchased": "רכש", "upsell": "מכירה נוספת",
    "cumulative_profit": "רווח מצטבר", "referred": "הפנה",
}


def campaigns(n: int, seed: int, *, weaken_answer: bool = False) -> pd.DataFrame:
    """Campaign rows that behave like the real thing: budget drives volume,
    volume drives closes, and profit follows closes with noise."""
    rng = np.random.default_rng(seed)
    budget = rng.choice([500, 1000, 2000, 3000, 5000, 8000, 12000], n,
                        p=[.12, .18, .22, .18, .15, .10, .05])
    leads = np.maximum(3, (budget / rng.uniform(28, 60, n)).round()).astype(int)
    answer_rate = np.clip(rng.normal(0.60, 0.12, n), 0.15, 0.95)
    if weaken_answer:
        answer_rate = np.where(budget >= 8000, answer_rate * 0.70, answer_rate)
    answered = np.minimum(leads, (leads * answer_rate).round()).astype(int)
    closed = np.minimum(answered, (answered * np.clip(
        rng.normal(0.22, 0.08, n), 0.01, 0.8)).round()).astype(int)
    df = pd.DataFrame({
        "ad_budget": budget, "num_leads": leads, "leads_answered": answered,
        "leads_not_answered": leads - answered,
        "followup_1": (answered * 0.8).round().astype(int),
        "followup_2": (answered * 0.6).round().astype(int),
        "followup_3": (answered * 0.4).round().astype(int),
        "followup_4": (answered * 0.2).round().astype(int),
        "followup_5": (answered * 0.08).round().astype(int),
        "closed": closed, "not_closed": answered - closed,
        "calls_to_closed": rng.integers(1, 8, n),
        "calls_to_not_closed": rng.integers(1, 9, n),
        "customer_acquisition_cost": (budget / np.maximum(closed, 1)).round().astype(int),
        "ltv_months": np.clip(rng.normal(9, 4, n), 1, 36).round(1),
        "purchased": closed > 0,
        "upsell": rng.random(n) < 0.18,
        "cumulative_profit": (closed * rng.normal(1800, 500, n)).round(1),
        "referred": np.where(rng.random(n) < 0.12, "Yes", "No"),
    })
    return df


def to_csv(df: pd.DataFrame, hebrew: bool = True) -> bytes:
    out = df.rename(columns=HEBREW_HEADERS) if hebrew else df
    buffer = io.BytesIO()
    out.to_csv(buffer, index=False, encoding="utf-8-sig")
    return buffer.getvalue()


# ───────────────────────────────────────────────────────── accounts
def signup(company: str) -> tuple[str, dict]:
    email = f"sim-{uuid.uuid4().hex[:8]}@funneliq.test"
    password = uuid.uuid4().hex + "Aa1!"
    user = requests.post(f"{URL}/auth/v1/admin/users", headers=ADMIN, json={
        "email": email, "password": password, "email_confirm": True,
        "user_metadata": {"company_name": company}}).json()
    CREATED.append(user["id"])
    token = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers=ADMIN,
                          json={"email": email, "password": password}).json()["access_token"]
    return email, {"Authorization": f"Bearer {token}"}


def cleanup() -> None:
    for user_id in CREATED:
        requests.delete(f"{URL}/auth/v1/admin/users/{user_id}", headers=ADMIN)
    from app.db import get_service_client
    client = get_service_client()
    kept = {p["company_id"] for p in client.table("profiles").select("company_id").execute().data}
    for company in client.table("companies").select("id").execute().data:
        if company["id"] not in kept:
            client.table("companies").delete().eq("id", company["id"]).execute()


def upload(auth, df, period, *, hebrew=True, mapping=None):
    data = {"period": period}
    if mapping:
        data["mapping"] = json.dumps(mapping)
    return requests.post(f"{API}/api/uploads", headers=auth,
                         files={"file": ("month.csv", to_csv(df, hebrew))}, data=data)


def get(auth, path):
    return requests.get(API + path, headers=auth)


# ══════════════════════════════════════════════════════════ the run
def main() -> None:
    print("SCENARIO 1 — a company signs up and finds an empty workspace")
    _, alice = signup("מאפיית הבוקר")
    me = get(alice, "/api/me").json()
    check("the workspace exists", me.get("company", {}).get("name") == "מאפיית הבוקר")
    check("it starts with no rows", me.get("recordCount") == 0)
    insights = get(alice, "/api/insights")
    check("insights answers instead of failing", insights.status_code == 200,
          f"HTTP {insights.status_code}")
    check("it says the workspace is empty", insights.json().get("empty") is True)
    check("no models yet", get(alice, "/api/models").json()["models"] == [])
    check("no analysis yet", get(alice, "/api/analysis").json()["run"] is None)
    check("scoring a record that is not there is 404",
          get(alice, "/api/predict/1").status_code == 404)

    print("\nSCENARIO 2 — a broken file is refused, and says why")
    broken = campaigns(50, 1).drop(columns=["closed"])
    r = requests.post(f"{API}/api/uploads/preview", headers=alice,
                      files={"file": ("x.csv", to_csv(broken))})
    body = r.json()
    check("the preview asks rather than fails", body.get("needsMapping") is True)
    asked = [q["column"] for q in body.get("questions", [])]
    check("it asks about the missing column", asked == ["closed"], str(asked))
    saved = upload(alice, broken, "2026-05")
    check("saving it is refused", saved.status_code == 422, f"HTTP {saved.status_code}")
    check("nothing was stored", get(alice, "/api/me").json()["recordCount"] == 0)

    text_file = requests.post(f"{API}/api/uploads/preview", headers=alice,
                              files={"file": ("x.csv", b"this is not a csv at all")})
    check("a file that is not a table is refused",
          text_file.status_code in (400, 422) or not text_file.json().get("ok"),
          f"HTTP {text_file.status_code}")

    print("\nSCENARIO 3 — a real month, with Hebrew headers")
    may = campaigns(220, 11)
    preview = requests.post(f"{API}/api/uploads/preview", headers=alice,
                            files={"file": ("m.csv", to_csv(may))}).json()
    check("all 19 columns recognised with no questions",
          preview["ok"] and not preview["needsMapping"],
          f"resolved {len(preview.get('resolved', {}))}/19")
    check("the answer column was not swapped with its opposite",
          preview["resolved"].get("leads_answered") == "ענו"
          and preview["resolved"].get("leads_not_answered") == "לא ענו")

    r = upload(alice, may, "2026-05")
    check("the month saves", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    saved_body = r.json()
    check("every row landed", saved_body.get("rows") == len(may), str(saved_body.get("rows")))

    print("\nSCENARIO 4 — do the dashboard numbers match the file?")
    ins = get(alice, "/api/insights").json()
    s, f = ins["summary"], ins["followup"]
    close("campaign count", s["total"], len(may), 0)
    close("average profit", s["avgProfit"], round(may.cumulative_profit.mean(), 1), 0.6)
    close("average LTV", s["avgLtvMonths"], round(may.ltv_months.mean(), 1), 0.15)
    close("upsell rate", s["upsellRate"], round(100 * may.upsell.mean(), 1), 0.15)
    close("referral rate", s["referralRate"],
          round(100 * (may.referred == "Yes").mean(), 1), 0.15)
    close("total leads in the funnel", f["funnel"][0]["count"], int(may.num_leads.sum()), 0)
    close("answered in the funnel", f["funnel"][1]["count"],
          int(may.leads_answered.sum()), 0)
    close("total spend", ins["budget"]["pot"], int(may.ad_budget.sum()), 0)
    check("the picker sees every campaign", len(ins["records"]) == len(may),
          f"{len(ins['records'])} of {len(may)}")
    check("the record count agrees with /api/me",
          get(alice, "/api/me").json()["recordCount"] == len(may))

    print("\nSCENARIO 5 — models trained on this company's own data")
    models = get(alice, "/api/models").json()
    check("models were trained", len(models["models"]) > 0, f"{len(models['models'])}")
    for m in models["models"]:
        check(f"  {m['target']}: measured against guessing",
              m["baseline"] is not None and m["error"] is not None)
        check(f"  {m['target']}: usable only if it beat guessing",
              m["useful"] == (m["betterByPct"] > 0),
              f"better {m['betterByPct']}% useful={m['useful']}")
    record_id = ins["records"][0]["id"]
    pred = get(alice, f"/api/predict/{record_id}").json()
    check("a campaign can be scored", "predictions" in pred)
    for target, p in pred.get("predictions", {}).items():
        check(f"  {target}: no number when the model is not usable",
              p["useful"] or p["value"] is None,
              f"useful={p['useful']} value={p['value']}")

    print("\nSCENARIO 6 — the same month twice, and a second month")
    again = upload(alice, may, "2026-05")
    check("re-uploading a month is refused", again.status_code == 409,
          f"HTTP {again.status_code}")
    check("nothing was doubled",
          get(alice, "/api/me").json()["recordCount"] == len(may))

    june = campaigns(240, 22, weaken_answer=True)
    r2 = upload(alice, june, "2026-06")
    check("the second month saves", r2.status_code == 200, f"HTTP {r2.status_code}")
    check("both months are stored",
          get(alice, "/api/me").json()["recordCount"] == len(may) + len(june))
    check("two uploads in the history", len(get(alice, "/api/me").json()["uploads"]) == 2)

    print("\nSCENARIO 7 — the narrative")
    analysis = get(alice, "/api/analysis").json()
    sentences = analysis["sentences"]
    check("a narrative was written", len(sentences) > 0, f"{len(sentences)} sentences")
    check("within the ceiling", len(sentences) <= 14)
    finding_ids = {f["finding_id"] for f in analysis["findings"]}
    check("every sentence names a finding that exists",
          all(s["finding_id"] in finding_ids for s in sentences))
    check("no sentence has an unfilled placeholder",
          all("{" not in s["text_he"] for s in sentences))
    check("no sentence is empty", all(s["text_he"].strip() for s in sentences))
    for s in sentences:
        print(f"      • {s['text_he']}")

    planted = any("מענה" in s["text_he"] or "ענו" in s["text_he"] for s in sentences)
    check("the planted answer-rate drop is mentioned", planted,
          "" if planted else "the weakened high-budget answering did not surface")

    print("\nSCENARIO 8 — undoing a month")
    upload_id = next(u for u in get(alice, "/api/me").json()["uploads"]
                     if u["period"].startswith("2026-06"))["id"]
    d = requests.delete(f"{API}/api/uploads/{upload_id}", headers=alice)
    check("the month deletes", d.status_code == 200, f"HTTP {d.status_code}")
    check("its rows went with it",
          get(alice, "/api/me").json()["recordCount"] == len(may),
          str(get(alice, "/api/me").json()["recordCount"]))
    total = get(alice, "/api/insights").json()["summary"]["total"]
    check("the dashboard follows", total == len(may), str(total))

    print("\nSCENARIO 9 — a second company sees none of it")
    _, bob = signup("סטודיו רוזן")
    b_me = get(bob, "/api/me").json()
    check("b starts empty", b_me["recordCount"] == 0)
    check("b's insights are empty", get(bob, "/api/insights").json()["empty"] is True)
    check("b has no models", get(bob, "/api/models").json()["models"] == [])
    check("b has no analysis", get(bob, "/api/analysis").json()["run"] is None)
    check("b cannot score a's campaign",
          get(bob, f"/api/predict/{record_id}").status_code == 404)
    check("b cannot delete a's upload",
          requests.delete(f"{API}/api/uploads/{upload_id}", headers=bob).status_code == 404)
    check("a still has its data",
          get(alice, "/api/me").json()["recordCount"] == len(may))

    print("\nSCENARIO 10 — a tiny company, below the training floor")
    _, tiny = signup("עסק קטן")
    upload(tiny, campaigns(6, 77), "2026-05")
    t_me = get(tiny, "/api/me").json()
    check("six rows are stored", t_me["recordCount"] == 6, str(t_me["recordCount"]))
    t_models = get(tiny, "/api/models").json()
    check("no model is offered on six rows", t_models["models"] == [],
          f"{len(t_models['models'])} models")
    t_ins = get(tiny, "/api/insights")
    check("the dashboard still works", t_ins.status_code == 200 and
          t_ins.json()["summary"]["total"] == 6)
    check("the analysis does not crash on one small month",
          get(tiny, "/api/analysis").status_code == 200)


if __name__ == "__main__":
    started = time.time()
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        FAILURES.append("the run raised before finishing")
    finally:
        cleanup()
    print(f"\n{'=' * 62}")
    if FAILURES:
        print(f"{len(FAILURES)} PROBLEM(S) in {time.time() - started:.0f}s\n")
        for line in FAILURES:
            print(f"  - {line}")
    else:
        print(f"every check passed in {time.time() - started:.0f}s")
    sys.exit(1 if FAILURES else 0)

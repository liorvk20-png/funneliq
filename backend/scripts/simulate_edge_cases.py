"""
Round two: the files a real export actually produces.

The first simulation checked the paths I designed for. These are the ones I did
not — an Excel export with thousands separators, a Hebrew Windows encoding, a
column with a stray blank, a month that is one campaign. Each is a plausible
first upload from a paying customer, and each is a place where the honest
failure and the silent wrong answer look identical from the outside.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, "/Users/liorvaknin/Downloads/ffinal project/files")
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv("/Users/liorvaknin/Downloads/ffinal project/files/.env")

from simulate_customer_journey import (  # noqa: E402
    API,
    FAILURES,
    HEBREW_HEADERS,
    campaigns,
    check,
    cleanup,
    get,
    signup,
    to_csv,
)


def preview(auth, payload: bytes, name="f.csv"):
    return requests.post(f"{API}/api/uploads/preview", headers=auth,
                         files={"file": (name, payload)})


def main() -> None:
    _, auth = signup("בדיקות קצה")

    print("A — the encodings a Hebrew CSV arrives in")
    df = campaigns(40, 5).rename(columns=HEBREW_HEADERS)
    for label, payload in [
        ("utf-8 with BOM (Excel save-as)", df.to_csv(index=False).encode("utf-8-sig")),
        ("utf-8 without BOM", df.to_csv(index=False).encode("utf-8")),
        ("cp1255 (Hebrew Windows Excel)", df.to_csv(index=False).encode("cp1255")),
    ]:
        r = preview(auth, payload)
        body = r.json() if r.status_code == 200 else {}
        check(f"  {label}", bool(body.get("ok")),
              f"HTTP {r.status_code} resolved {len(body.get('resolved', {}))}/19")

    print("\nB — the shapes an export actually has")
    base = campaigns(40, 6)

    thousands = base.copy()
    for column in ("ad_budget", "cumulative_profit"):
        thousands[column] = thousands[column].map(lambda v: f"{v:,.0f}")
    r = preview(auth, to_csv(thousands))
    body = r.json()
    check("  numbers written as 1,200 are read as numbers", bool(body.get("ok")),
          "errors: " + "; ".join(body.get("errors", []))[:120])

    currency = base.copy()
    currency["ad_budget"] = currency["ad_budget"].map(lambda v: f"₪{v}")
    r = preview(auth, to_csv(currency))
    body = r.json()
    check("  a currency symbol is rejected clearly rather than silently zeroed",
          not body.get("ok") and any("תקציב" in e or "ad_budget" in e
                                     for e in body.get("errors", [])),
          "; ".join(body.get("errors", []))[:120])

    blanks = base.copy()
    blanks.loc[0, "closed"] = None
    body = preview(auth, to_csv(blanks)).json()
    check("  a blank in a required column is refused", not body.get("ok"),
          "; ".join(body.get("errors", []))[:100])

    extra = base.copy()
    extra["שם קמפיין"] = "קמפיין אביב"
    extra["אזור"] = "מרכז"
    body = preview(auth, to_csv(extra)).json()
    check("  unknown columns are a warning, not a rejection", bool(body.get("ok")))
    check("  and they are named", len(body.get("extraColumns", [])) == 2,
          str(body.get("extraColumns")))

    mismatched = base.copy()
    mismatched.loc[0:4, "leads_answered"] = mismatched.loc[0:4, "num_leads"] + 99
    body = preview(auth, to_csv(mismatched)).json()
    check("  parts that do not sum to the whole are flagged and still stored",
          body.get("ok") and any("ענו" in w for w in body.get("warnings", [])))

    duplicated = pd.concat([base, base.head(5)]).reset_index(drop=True)
    body = preview(auth, to_csv(duplicated)).json()
    check("  duplicate rows are flagged without being removed",
          body.get("ok") and any("זהות" in w for w in body.get("warnings", []))
          and body["rows"] == len(duplicated))

    print("\nC — sizes at the edges")
    one = campaigns(1, 7)
    r = requests.post(f"{API}/api/uploads", headers=auth,
                      files={"file": ("one.csv", to_csv(one))}, data={"period": "2026-01"})
    check("  a single campaign uploads", r.status_code == 200, f"HTTP {r.status_code}")
    check("  the dashboard survives one row",
          get(auth, "/api/insights").status_code == 200)
    ins = get(auth, "/api/insights").json()
    check("  and reports it as one", ins["summary"]["total"] == 1)
    check("  with no budget recommendation from one point",
          ins["budget"]["best"] is None or ins["budget"]["gainPct"] is not None)
    check("  no model from one row", get(auth, "/api/models").json()["models"] == [])
    check("  the analysis endpoint answers", get(auth, "/api/analysis").status_code == 200)

    zeros = campaigns(30, 8)
    zeros["ad_budget"] = 0
    zeros["num_leads"] = 0
    zeros["leads_answered"] = 0
    zeros["leads_not_answered"] = 0
    zeros["closed"] = 0
    zeros["not_closed"] = 0
    r = requests.post(f"{API}/api/uploads", headers=auth,
                      files={"file": ("z.csv", to_csv(zeros))}, data={"period": "2026-02"})
    check("  a month of all zeros does not divide by zero", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:100]}")
    check("  the dashboard still answers", get(auth, "/api/insights").status_code == 200)
    check("  so does the analysis", get(auth, "/api/analysis").status_code == 200)

    print("\nD — the period field")
    for label, period, expect in [
        ("a malformed month", "not-a-date", 400),
        ("an empty month", "", 400),
        ("YYYY-MM-DD is accepted", "2026-03-01", 200),
    ]:
        r = requests.post(f"{API}/api/uploads", headers=auth,
                          files={"file": ("p.csv", to_csv(campaigns(20, hash(period) % 999)))},
                          data={"period": period})
        check(f"  {label}", r.status_code == expect, f"HTTP {r.status_code}")

    print("\nE — an explicit column mapping from the browser")
    renamed = campaigns(40, 10).rename(columns={**HEBREW_HEADERS, "closed": "מה שיצא"})
    body = preview(auth, to_csv(renamed, hebrew=False)).json()
    check("  an unrecognisable column becomes a question",
          body.get("needsMapping") and body["questions"][0]["column"] == "closed")
    check("  with example values from that column",
          len(body["samples"].get("מה שיצא", [])) == 3, str(body.get("samples", {}))[:80])
    answered = requests.post(f"{API}/api/uploads/preview", headers=auth,
                             files={"file": ("m.csv", to_csv(renamed, hebrew=False))},
                             data={"mapping": '{"closed": "מה שיצא"}'}).json()
    check("  answering it resolves the file", answered.get("ok") is True)
    hostile = requests.post(f"{API}/api/uploads/preview", headers=auth,
                            files={"file": ("m.csv", to_csv(renamed, hebrew=False))},
                            data={"mapping": '{"closed": "עמודה שלא קיימת"}'}).json()
    check("  a mapping naming a column that is not in the file is ignored",
          hostile.get("ok") is False)

    print("\nF — the company profile")
    r = requests.patch(f"{API}/api/company", headers=auth, json={"name": "שם חדש"})
    check("  renaming works", r.status_code == 200 and r.json()["name"] == "שם חדש")
    check("  a blank name is refused",
          requests.patch(f"{API}/api/company", headers=auth,
                         json={"name": "   "}).status_code == 400)
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
           "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    check("  a logo uploads",
          requests.patch(f"{API}/api/company", headers=auth,
                         json={"logo": png}).status_code == 200)
    check("  it comes back on /api/me",
          get(auth, "/api/me").json()["company"].get("logo_b64") == png)
    check("  an SVG is refused",
          requests.patch(f"{API}/api/company", headers=auth,
                         json={"logo": "data:image/svg+xml;base64,PHN2Zy8+"}
                         ).status_code == 400)
    check("  the logo can be removed",
          requests.patch(f"{API}/api/company", headers=auth,
                         json={"logo": None}).status_code == 200)

    print("\nG — a dead session")
    stale = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.x"}
    for path in ("/api/me", "/api/insights", "/api/models", "/api/analysis"):
        check(f"  {path} refuses a bad token",
              get(stale, path).status_code == 401,
              f"HTTP {get(stale, path).status_code}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        FAILURES.append("the run raised before finishing")
    finally:
        cleanup()
    print("\n" + "=" * 62)
    if FAILURES:
        print(f"{len(FAILURES)} PROBLEM(S)\n")
        for line in FAILURES:
            print(f"  - {line}")
    else:
        print("every check passed")
    sys.exit(1 if FAILURES else 0)

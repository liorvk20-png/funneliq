"""
Load a CSV of campaigns into one company's workspace.

Usage (from the repo root, with .env populated):
    python scripts/load_csv_to_supabase.py --company someone@example.com
    python scripts/load_csv_to_supabase.py --company someone@example.com --csv other.csv

The --company argument is required and it is the whole point of this file. The
previous version predated multi-tenancy: it inserted rows with no company and
began with

    client.table("funnel_records").delete().neq("id", 0)

which, once several companies shared the table, would have deleted every
customer's data on every run. Scoping both the delete and the insert to one
company is what makes the script safe to keep around.

It replaces that company's rows rather than appending, so re-running after a
change to the CSV never duplicates anything — and it leaves a row in `uploads`,
so the history of what was loaded when is visible in the product itself.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Running this as a script puts scripts/ on sys.path, not the repo root, so
# `import app` would fail. Put the repo root first so the documented usage
# above works as written.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from app.db import get_service_client  # noqa: E402  (import after load_dotenv on purpose)


def resolve_company(client, email: str) -> tuple[str, str]:
    """The company behind a user's email, or a message explaining what to do."""
    profiles = client.table("profiles").select("company_id").eq("email", email).execute().data
    if not profiles:
        raise SystemExit(
            f"No profile for {email}.\n"
            "Either the account does not exist, or it was created before the "
            "sign-up trigger and has no workspace yet. Run "
            "migrations/003_backfill_profiles.sql in the Supabase SQL editor."
        )
    company_id = profiles[0]["company_id"]
    name = client.table("companies").select("name").eq("id", company_id).execute().data[0]["name"]
    return company_id, name


def main(csv_path: str, email: str, period: date) -> None:
    df = pd.read_csv(csv_path)

    df["purchased"] = df["purchased"].astype(bool)
    df["upsell"] = df["upsell"].astype(bool)
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    # ltv_months / cumulative_profit NaNs pass through as None -> SQL NULL.
    # Cleaning/imputation decisions belong in Package 1's analysis, not here —
    # this script's job is a faithful load, not silent data repair.

    # .astype(object) BEFORE .where() matters: on a float64 column pandas coerces
    # None straight back to NaN, so the NaNs would survive and json.dumps would
    # emit a bare `NaN` token — invalid JSON that PostgREST rejects. Casting to
    # object dtype first lets the column actually hold None, which serialises to
    # JSON null and lands in Postgres as SQL NULL.
    records = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")

    client = get_service_client()  # service key: bypasses RLS, script-only
    company_id, company_name = resolve_company(client, email)

    upload = client.table("uploads").insert({
        "company_id": company_id,
        "period": period.isoformat(),
        "filename": Path(csv_path).name,
        "row_count": len(records),
        "status": "analysing",
    }).execute().data[0]

    for r in records:
        r["company_id"] = company_id
        r["upload_id"] = upload["id"]

    # Scoped to this company. Without the eq() every other customer's rows go
    # with it — the single most damaging line this script could contain.
    client.table("funnel_records").delete().eq("company_id", company_id).execute()

    batch_size = 500
    try:
        for i in range(0, len(records), batch_size):
            client.table("funnel_records").insert(records[i : i + batch_size]).execute()
    except Exception as exc:
        # A half-finished load that still says "ready" is worse than a visible
        # failure, because the dashboard would present partial data as complete.
        client.table("uploads").update(
            {"status": "failed", "error": str(exc)[:500]}
        ).eq("id", upload["id"]).execute()
        raise

    client.table("uploads").update({"status": "ready"}).eq("id", upload["id"]).execute()
    print(f"Loaded {len(records):,} rows into {company_name} ({email}).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--company", required=True, metavar="EMAIL",
                   help="email of a user in the company that should own these rows")
    p.add_argument("--csv", default="funnel_marketing_data.csv")
    p.add_argument("--period", default=date.today().replace(day=1).isoformat(),
                   help="the month this data describes, YYYY-MM-DD (default: this month)")
    a = p.parse_args()
    main(a.csv, a.company, date.fromisoformat(a.period))

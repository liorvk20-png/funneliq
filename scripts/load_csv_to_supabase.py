"""
Repeatable loader: funnel_marketing_data.csv -> Supabase `funnel_records` table.
Safe to re-run any time the source CSV changes — it clears the table and
reloads rather than appending, so re-running never duplicates rows.

Usage (from the repo root, with .env populated):
    python scripts/load_csv_to_supabase.py funnel_marketing_data.csv
"""
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Running this as a script puts scripts/ on sys.path, not the repo root, so
# `import app` would fail. Put the repo root first so the documented usage
# above (`python scripts/load_csv_to_supabase.py ...`) works as written.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from app.db import get_service_client  # noqa: E402  (import after load_dotenv on purpose)


def main(csv_path: str) -> None:
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
    client.table("funnel_records").delete().neq("id", 0).execute()  # clear previous load

    batch_size = 500
    for i in range(0, len(records), batch_size):
        client.table("funnel_records").insert(records[i : i + batch_size]).execute()

    print(f"Loaded {len(records)} rows into funnel_records.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "funnel_marketing_data.csv")

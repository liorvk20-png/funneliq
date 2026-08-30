"""
Shared fixtures.

The tests never touch Supabase or the network. Everything either reads the
committed CSV or feeds hand-built rows through the same functions the API uses,
so the suite runs identically on a laptop and in CI with no secrets.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

# app.db and app.auth read these at import; the values only need to be
# well-formed, since nothing here makes a request.
import os  # noqa: E402

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")

CSV = ROOT / "funnel_marketing_data.csv"


@pytest.fixture(scope="session")
def csv() -> pd.DataFrame:
    return pd.read_csv(CSV)


@pytest.fixture(scope="session")
def rows(csv) -> list[dict]:
    """The CSV shaped the way the database returns it, for the aggregate tests."""
    df = csv.copy()
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    df["purchased"] = df["purchased"].astype(bool)
    df["upsell"] = df["upsell"].astype(bool)
    df["budget_tier"] = df["ad_budget"].map(
        lambda b: "Low" if b <= 1500 else ("Mid" if b <= 5000 else "High"))
    df.insert(0, "id", range(1, len(df) + 1))
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")

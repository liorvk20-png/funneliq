"""
One definition of the feature set, imported by every modelling package.

The leakage rule is enforced here rather than remembered in six places: the
four outcome columns are removed from the feature frame by construction, so a
package cannot accidentally train on one while predicting another.
"""
from pathlib import Path

import pandas as pd

CSV = Path(__file__).resolve().parent.parent / "funnel_marketing_data.csv"

# Locked by the project rule. Outcomes of the funnel — target only, never input.
OUTCOMES = ["upsell", "referred", "cumulative_profit", "ltv_months"]

TIERS = ["Low", "Mid", "High"]


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    df["purchased"] = df["purchased"].astype(bool)
    df["upsell"] = df["upsell"].astype(bool)
    df["budget_tier"] = pd.Categorical(
        df["ad_budget"].map(lambda b: "Low" if b <= 1500 else ("Mid" if b <= 5000 else "High")),
        categories=TIERS,
        ordered=True,
    )
    return df


def features(df: pd.DataFrame, target: str, *, drop: list[str] | None = None) -> pd.DataFrame:
    """
    Every column except the four outcomes, so the rule holds whichever outcome
    is being predicted. `drop` removes further columns for a specific
    experiment — used to measure how much one feature is carrying.

    budget_tier becomes its ordinal position: it is a deterministic function of
    ad_budget, so a tree could recover it unaided, but keeping it explicit lets
    feature importance report tier effects directly, which is what the brief
    asks Package 1 and Package 6 to talk about.
    """
    if target not in OUTCOMES:
        raise ValueError(f"{target} is not one of the four outcomes: {OUTCOMES}")
    X = df.drop(columns=OUTCOMES + (drop or []))
    X["budget_tier"] = X["budget_tier"].cat.codes
    return X


def target_frame(df: pd.DataFrame, target: str, *, drop: list[str] | None = None):
    """
    X, y with rows whose target is missing removed — and only those. A row with
    a missing *feature* stays: the gradient-boosting models all handle that
    natively, and dropping it would throw away a usable example.
    """
    usable = df[df[target].notna()]
    return features(usable, target, drop=drop), usable[target].astype(float)

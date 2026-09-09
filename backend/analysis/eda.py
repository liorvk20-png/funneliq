"""
Package 1 — exploratory analysis and cleaning decisions.

Reads the source CSV directly rather than the database on purpose: this is the
record of what the raw data looked like before anything downstream touched it,
so it must not depend on the state of a table that gets reloaded.

Run from the repo root:  python analysis/eda.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CSV = Path(__file__).resolve().parent.parent / "funnel_marketing_data.csv"

# Locked by the project's data-leakage rule: each of these is an outcome of the
# funnel, so none may ever be a feature when predicting another. Listed here so
# the modelling packages import one definition instead of re-deriving it.
OUTCOMES = ["upsell", "referred", "cumulative_profit", "ltv_months"]


def tier(budget: int) -> str:
    """Low <=1500, Mid 1501-5000, High >5000 — matches the generated column in schema.sql."""
    return "Low" if budget <= 1500 else ("Mid" if budget <= 5000 else "High")


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    df["purchased"] = df["purchased"].astype(bool)
    df["upsell"] = df["upsell"].astype(bool)
    df["budget_tier"] = pd.Categorical(
        df["ad_budget"].map(tier), categories=["Low", "Mid", "High"], ordered=True
    )
    return df


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> None:
    df = load()

    rule("1. SHAPE AND COMPLETENESS")
    print(f"rows: {len(df):,}   columns: {df.shape[1]}")
    miss = df.isna().sum()
    miss = miss[miss > 0]
    if miss.empty:
        print("no missing values")
    else:
        for col, n in miss.items():
            print(f"  {col:<22} {n:>3} missing  ({n / len(df) * 100:.2f}%)")
    print("\nDecision: left as NULL, not imputed. The gradient-boosting models this")
    print("project uses handle missing features natively, so filling them in would")
    print("invent data to solve a problem the models do not have. A row missing the")
    print("value being predicted is dropped for that model only.")

    rule("2. BUDGET TIER PERFORMANCE")
    g = df.groupby("budget_tier", observed=True)
    tbl = pd.DataFrame({
        "campaigns": g.size(),
        "avg_budget": g["ad_budget"].mean(),
        "avg_leads": g["num_leads"].mean(),
        "close_rate_%": 100 * g["closed"].sum() / g["num_leads"].sum(),
        "avg_CAC": g["customer_acquisition_cost"].mean(),
        "avg_LTV_mo": g["ltv_months"].mean(),
        "avg_profit": g["cumulative_profit"].mean(),
        "upsell_%": g["upsell"].mean() * 100,
        "referral_%": g["referred"].mean() * 100,
    })
    print(tbl.round(1).to_string())

    print("\nProfit per shekel of ad spend — the figure Package 6 optimises:")
    roi = (g["cumulative_profit"].sum() / g["ad_budget"].sum()).round(2)
    for t, v in roi.items():
        print(f"  {t:<6} {v:>6.2f}x")

    rule("3. THE FUNNEL, STAGE BY STAGE")
    stages = [
        ("leads", df["num_leads"].sum()),
        ("answered", df["leads_answered"].sum()),
        ("follow-up 1", df["followup_1"].sum()),
        ("follow-up 2", df["followup_2"].sum()),
        ("follow-up 3", df["followup_3"].sum()),
        ("follow-up 4", df["followup_4"].sum()),
        ("follow-up 5", df["followup_5"].sum()),
        ("closed", df["closed"].sum()),
    ]
    top = stages[0][1]
    prev = None
    print(f"{'stage':<14}{'count':>10}{'of leads':>11}{'kept from prev':>16}")
    for name, n in stages:
        step = f"{n / prev * 100:>14.1f}%" if prev else f"{'—':>15}"
        print(f"{name:<14}{n:>10,}{n / top * 100:>10.1f}%{step}")
        prev = n

    rule("4. THE FOLLOW-UP PARADOX (Package 5 preview)")
    print("Does making more calls actually help? Grouped by calls to a closed deal:")
    closed = df[df["closed"] > 0]
    p = closed.groupby("calls_to_closed").agg(
        campaigns=("closed", "size"),
        avg_LTV=("ltv_months", "mean"),
        avg_profit=("cumulative_profit", "mean"),
        upsell_rate=("upsell", "mean"),
    )
    p["upsell_rate"] = (p["upsell_rate"] * 100).round(1)
    print(p.round(1).to_string())

    rule("5. THE FIVE OUTLIERS — KEPT, ON RECORD")
    out = df[df["cumulative_profit"] > 100_000]
    cp = df["cumulative_profit"].dropna()
    print(f"99th percentile of profit: {cp.quantile(0.99):,.0f}")
    print(f"highest value below 100k : {cp[cp <= 100_000].max():,.0f}")
    print(f"\n{len(out)} rows above 100,000:")
    print(out[["ad_budget", "budget_tier", "closed", "ltv_months",
               "cumulative_profit", "upsell", "referred"]].to_string(index=False))
    without = cp[cp <= 100_000]
    print(f"\nstd with them: {cp.std():,.0f}   without: {without.std():,.0f}"
          f"   inflation: {cp.std() / without.std():.2f}x")
    print("\nDecision: kept. They are internally consistent — every one has an LTV of")
    print("42-56 months against a median near 30, and every one was referred, so they")
    print("read as genuine long-lived customers rather than data entry errors. Removing")
    print("real business outcomes because they are inconvenient would bias the models")
    print("against exactly the customers the agency most wants to find. Their effect is")
    print("also modest: they widen the spread by a tenth, not an order of magnitude.")
    print("Consequence to carry forward: report MAE alongside RMSE in Packages 2 and 6,")
    print("since squared error lets five rows dominate a 3,500-row score.")

    rule("6. LEAKAGE GUARD")
    print("These four columns are outcomes of the funnel, never inputs to each other:")
    print(f"  {', '.join(OUTCOMES)}")
    print("\nCorrelation among them — the reason the rule exists:")
    print(df[OUTCOMES].astype(float).corr().round(2).to_string())
    print("\nupsell and cumulative_profit correlate strongly. A model predicting profit")
    print("that was handed `upsell` would score beautifully and be useless: at the moment")
    print("a budget decision is actually made, nobody knows yet whether the upsell happens.")


if __name__ == "__main__":
    main()

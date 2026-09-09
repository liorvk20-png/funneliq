"""
Package 6 — budget allocation simulator.

The trap this package has to avoid: the profit model from the other packages
reads num_leads, closed and calls_to_closed, none of which exist at the moment
a budget is set. A simulator built on them would be answering "given how the
campaign went, what did it earn" — a question nobody planning a budget can ask.

So the planning model here sees only the decision variable, ad_budget, and the
tier it falls into. That is a weaker model on purpose; it is the one whose
inputs a planner actually has.

Run from the repo root:  python analysis/package6_budget.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_raw

warnings.filterwarnings("ignore")
TOTAL = 1_000_000  # the pot a strategy has to allocate


def curve(df: pd.DataFrame) -> pd.DataFrame:
    """Observed return at each budget level actually run — the planner's evidence."""
    g = df.groupby("ad_budget")
    c = pd.DataFrame({
        "campaigns": g.size(),
        "avg_profit": g["cumulative_profit"].mean(),
        "sd_profit": g["cumulative_profit"].std(),
    })
    c["profit_per_shekel"] = c["avg_profit"] / c.index
    c["tier"] = ["Low" if b <= 1500 else ("Mid" if b <= 5000 else "High") for b in c.index]
    return c


def simulate(c: pd.DataFrame, budget_level: int, pot: int = TOTAL) -> dict:
    """Spend the whole pot on campaigns of one size, and see what comes back."""
    n = pot // budget_level
    row = c.loc[budget_level]
    profit = n * row.avg_profit
    # Campaign outcomes vary; running more of them averages that variation down,
    # so the same pot split into more campaigns is a steadier bet as well as a
    # different-sized one. Reporting it keeps "best" from meaning only "highest".
    se = row.sd_profit * np.sqrt(n) if not np.isnan(row.sd_profit) else np.nan
    return {"budget_each": budget_level, "campaigns": int(n), "spent": int(n * budget_level),
            "expected_profit": profit, "return_per_shekel": profit / pot,
            "risk_sd": se, "profit_per_risk": profit / se if se and se > 0 else np.nan}


def main() -> None:
    df = load_raw()

    print("=" * 82)
    print("1. WHAT EACH BUDGET LEVEL ACTUALLY RETURNED")
    print("=" * 82)
    c = curve(df)
    print(f"{'budget':>8}{'campaigns':>11}{'avg profit':>13}{'per shekel':>12}  tier")
    for b, r in c.iterrows():
        mark = "  <-- peak" if r.profit_per_shekel == c.profit_per_shekel.max() else ""
        print(f"{b:>8,}{int(r.campaigns):>11,}{r.avg_profit:>13,.0f}"
              f"{r.profit_per_shekel:>11.2f}x  {r.tier}{mark}")

    print("\n" + "=" * 82)
    print("2. THE RETURN CURVE IS NOT A LINE")
    print("=" * 82)
    peak = c.profit_per_shekel.idxmax()
    for b, r in c.iterrows():
        width = round(r.profit_per_shekel / c.profit_per_shekel.max() * 46)
        print(f"{b:>8,}  {'#' * width}{'' if width else '·'} {r.profit_per_shekel:.2f}x")
    print(f"\nReturn peaks at {peak:,} and falls away on both sides. Spending less than")
    print("that leaves money unearned; spending more buys progressively worse outcomes.")
    print("Anything under 1.00x returns less profit than it consumed in ad spend.")
    losing = c[c.profit_per_shekel < 1.0]
    if len(losing):
        print(f"Levels below 1.00x: {', '.join(f'{b:,}' for b in losing.index)}")

    print("\n" + "=" * 82)
    print(f"3. ALLOCATION STRATEGIES FOR A {TOTAL:,} POT")
    print("=" * 82)
    strategies = {}
    for b in c.index:
        strategies[f"all at {b:,}"] = simulate(c, b)
    sim = pd.DataFrame(strategies).T

    # A realistic comparison point: keep spending in the same proportions the
    # agency already uses, rather than against a strawman.
    weights = df.groupby("ad_budget")["ad_budget"].sum() / df["ad_budget"].sum()
    current = sum(simulate(c, b)["expected_profit"] * w for b, w in weights.items())
    print(f"{'strategy':<20}{'campaigns':>11}{'expected profit':>18}{'per shekel':>13}")
    print("-" * 82)
    print(f"{'current mix':<20}{'—':>11}{current:>18,.0f}{current / TOTAL:>12.2f}x")
    for name, r in sim.sort_values("expected_profit", ascending=False).head(6).iterrows():
        print(f"{name:<20}{int(r.campaigns):>11,}{r.expected_profit:>18,.0f}"
              f"{r.return_per_shekel:>12.2f}x")
    print("...")
    worst = sim.sort_values("expected_profit").head(2)
    for name, r in worst.iterrows():
        print(f"{name:<20}{int(r.campaigns):>11,}{r.expected_profit:>18,.0f}"
              f"{r.return_per_shekel:>12.2f}x")

    best_name = sim["expected_profit"].idxmax()
    best = sim.loc[best_name]
    gain = best.expected_profit - current
    print(f"\nBest single-level strategy: {best_name}")
    print(f"  {best.expected_profit:,.0f} against {current:,.0f} for the current mix")
    print(f"  a gain of {gain:,.0f} ({gain / current * 100:.0f}%) on the same {TOTAL:,}")

    print("\n" + "=" * 82)
    print("4. THE HONEST CAVEATS")
    print("=" * 82)
    n_best = int(best.campaigns)
    lvl = int(best.budget_each)
    n_observed = int(c.loc[lvl, "campaigns"])
    sd_at = c.loc[lvl, "sd_profit"]
    mean_at = c.loc[lvl, "avg_profit"]
    gain_pct = gain / current * 100
    print(f"""
a) This assumes the market absorbs {n_best:,} campaigns at {lvl:,} each.
   Nothing in the data speaks to saturation, and it is the most likely way this
   recommendation fails in practice. The dataset holds {n_observed:,} campaigns at
   that level, not {n_best:,}.

b) Campaigns at the same budget vary widely. At {lvl:,} the spread is
   {sd_at:,.0f} against a mean of {mean_at:,.0f}, so any single campaign
   is a poor bet even where the average is excellent. The case for the level
   rests on running many.

c) Budget is the only lever modelled. Package 5 found the top of the funnel
   loses 39% of leads before any follow-up; fixing that changes every number
   in this table and is not a budget decision at all.

d) The five extreme profits from Package 1 are included, as decided. They sit
   in the Low and Mid tiers and lift those averages slightly.
""")

    print("=" * 82)
    print("RECOMMENDATION")
    print("=" * 82)
    print(f"""
Move spend toward the {c.loc[peak, 'tier']} tier and out of High.

The return curve peaks at {peak:,} per campaign ({c.profit_per_shekel.max():.2f}x) and
degrades steadily above it. The largest budgets in the book return under 1.00x
— they consume more in ad spend than they produce in profit.

On a {TOTAL:,} pot, concentrating at {lvl:,} returns {best.expected_profit:,.0f}
against {current:,.0f} for the current mix: {gain_pct:.0f}% more from the same money,
with no new spend and no change to how the funnel is run.

Do it as a shift, not a switch. Caveat (a) is the real risk: the recommendation
assumes demand at that level scales, and the data cannot show that. Move a
quarter of High-tier spend down a tier, measure for a quarter, and continue only
if the curve holds.
""")


if __name__ == "__main__":
    main()

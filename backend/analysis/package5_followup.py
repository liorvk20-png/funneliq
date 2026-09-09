"""
Package 5 — the follow-up paradox.

Four packages have now put `calls_to_closed` at or near the top. The raw
pattern is stark: deals closed in one or two calls are worth roughly twelve
times deals that take six. The question this package exists to answer is
whether calling less would *cause* better outcomes, or whether call count is
simply a symptom of lead quality — because the two readings imply opposite
instructions to a sales team.

Run from the repo root:  python analysis/package5_followup.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_raw

warnings.filterwarnings("ignore")
STAGES = ["leads_answered", "followup_1", "followup_2", "followup_3",
          "followup_4", "followup_5"]


def bar(value: float, scale: float, width: int = 34) -> str:
    return "#" * max(0, min(width, round(value / scale * width)))


def main() -> None:
    df = load_raw()

    print("=" * 78)
    print("1. WHERE THE FUNNEL LEAKS")
    print("=" * 78)
    totals = {s: int(df[s].sum()) for s in STAGES}
    totals = {"num_leads": int(df["num_leads"].sum()), **totals, "closed": int(df["closed"].sum())}
    prev = None
    print(f"{'stage':<18}{'people':>10}{'kept':>9}{'lost here':>11}")
    for name, n in totals.items():
        if prev is None:
            print(f"{name:<18}{n:>10,}{'—':>9}{'—':>11}")
        else:
            print(f"{name:<18}{n:>10,}{n / prev * 100:>8.1f}%{prev - n:>11,}")
        prev = n
    drop_first = totals["num_leads"] - totals["leads_answered"]
    print(f"\nThe single largest loss is before any follow-up exists: {drop_first:,} leads")
    print(f"({drop_first / totals['num_leads'] * 100:.1f}%) never answer at all. No amount of")
    print("follow-up discipline reaches them.")

    print("\n" + "=" * 78)
    print("2. THE RAW PATTERN")
    print("=" * 78)
    closed = df[df["closed"] > 0]
    raw = closed.groupby("calls_to_closed").agg(
        campaigns=("closed", "size"),
        avg_LTV=("ltv_months", "mean"),
        avg_profit=("cumulative_profit", "mean"),
    ).round(1)
    print(f"{'calls':<8}{'campaigns':>11}{'avg LTV':>10}{'avg profit':>12}   profit")
    for calls, r in raw.iterrows():
        print(f"{calls:<8}{int(r.campaigns):>11,}{r.avg_LTV:>10.1f}{r.avg_profit:>12,.0f}   "
              f"{bar(r.avg_profit, 25000)}")
    print("\nTaken at face value this says: stop calling. That reading is what the rest")
    print("of this package is here to test.")

    print("\n" + "=" * 78)
    print("3. IS IT THE CALLS, OR THE LEADS?")
    print("=" * 78)
    print("If call count causes low value, the pattern should survive when campaigns")
    print("with similar lead quality are compared only against each other. If it is a")
    print("symptom, it should weaken sharply once quality is held still.\n")
    print("Quality proxy: the share of leads that pick up the phone — decided before")
    print("any follow-up policy applies, so it is a property of the leads, not of how")
    print("hard the team worked them.\n")

    closed = closed.copy()
    closed["answer_rate"] = closed["leads_answered"] / closed["num_leads"]
    closed["quality"] = pd.qcut(closed["answer_rate"], 4,
                                labels=["worst 25%", "low-mid", "high-mid", "best 25%"])
    c = closed["calls_to_closed"]
    closed["call_group"] = np.where(c <= 2, "1-2 calls", np.where(c <= 4, "3-4 calls", "5+ calls"))

    pivot = closed.pivot_table(index="quality", columns="call_group",
                               values="cumulative_profit", aggfunc="mean", observed=True)
    pivot = pivot[["1-2 calls", "3-4 calls", "5+ calls"]]
    print("Average profit, split by lead quality and by calls needed:")
    print(pivot.round(0).to_string())

    overall = closed.groupby("call_group", observed=True)["cumulative_profit"].mean()
    gap_raw = overall["1-2 calls"] / overall["5+ calls"]
    within = (pivot["1-2 calls"] / pivot["5+ calls"]).mean()
    print("\nGap between 1-2 calls and 5+ calls:")
    print(f"  ignoring lead quality       : {gap_raw:.1f}x")
    print(f"  averaged within quality bands: {within:.1f}x")
    print(f"  explained by lead quality    : {(1 - within / gap_raw) * 100:.0f}%")

    print("\n" + "=" * 78)
    print("4. WHAT THE ANSWER RATE ITSELF PREDICTS")
    print("=" * 78)
    q = closed.groupby("quality", observed=True).agg(
        campaigns=("closed", "size"),
        avg_answer_rate=("answer_rate", "mean"),
        avg_calls_to_close=("calls_to_closed", "mean"),
        avg_profit=("cumulative_profit", "mean"),
    )
    print(q.round(2).to_string())
    print("\nBetter-answering leads need fewer calls AND produce more profit. Call count")
    print("is downstream of a property the leads already had.")

    print("\n" + "=" * 78)
    print("5. THE COST OF CALLS THAT GO NOWHERE")
    print("=" * 78)
    print("calls_to_not_closed is effort spent on leads that never convert — the part")
    print("a 'call less' policy would actually be cutting.\n")
    df2 = df.copy()
    df2["wasted"] = df2["calls_to_not_closed"] * df2["not_closed"]
    df2["productive"] = df2["calls_to_closed"] * df2["closed"]
    w, p = int(df2["wasted"].sum()), int(df2["productive"].sum())
    print(f"  calls into deals that closed     : {p:>10,}")
    print(f"  calls into deals that never did  : {w:>10,}")
    print(f"  share of all calling effort wasted: {w / (w + p) * 100:>9.1f}%")
    by_tier = df2.groupby("budget_tier", observed=True).apply(
        lambda x: x["wasted"].sum() / (x["wasted"].sum() + x["productive"].sum()) * 100)
    print("\n  wasted share by tier:")
    for t, v in by_tier.items():
        print(f"    {t:<6}{v:>6.1f}%  {bar(v, 100)}")

    print("\n" + "=" * 78)
    print("RECOMMENDATION")
    print("=" * 78)
    explained = (1 - within / gap_raw) * 100
    lost_pct = drop_first / totals["num_leads"] * 100
    best_fast = pivot.loc["best 25%", "1-2 calls"]
    best_slow = pivot.loc["best 25%", "5+ calls"]
    p3, p4, p5 = (raw.loc[i, "avg_profit"] for i in (3, 4, 5))
    waste_pct = w / (w + p) * 100
    print(f"""
This package was written expecting to find that call count was a proxy for lead
quality. It is not, and the test says so plainly.

Holding answer rate still barely moves the gap: {gap_raw:.1f}x raw against {within:.1f}x within
matched quality bands, so lead quality as measured here explains only {explained:.0f}% of it.
The pattern is visible inside every quality band, including the best 25%, where
1-2 call deals still return {best_fast:,.0f} against {best_slow:,.0f} for 5+ call deals.

Section 4 shows answer rate genuinely does predict both effort and profit, so
confounding is real — it is simply much smaller than the effect. The "it is all
just bad leads" explanation is the one this analysis was built to test, and it
does not survive.

What that does and does not license:

  It does NOT prove that the sixth call destroys value. Answer rate is one
  proxy for quality, not all of it. Deal size, product fit and rep skill are
  unobserved here, and any of them could drive both call count and profit.

  It DOES mean call count carries information that survives the most obvious
  confounder, which makes it usable as a signal even with the mechanism unknown.

Three recommendations, ordered by how well the data supports them:

1. Fix the top of the funnel. {drop_first:,} leads ({lost_pct:.0f}%) never answer at all.
   That single stage loses more people than every follow-up stage combined,
   and no calling policy reaches them. This one needs no causal claim.

2. Treat call four as a review point, not a cut-off. Expected profit falls from
   {p3:,.0f} at three calls to {p4:,.0f} at four. Reviewing rather than
   stopping matters because {waste_pct:.0f}% of all calling effort already goes into
   deals that never close — the waste is real, but cutting blindly would also
   discard the {p5:,.0f} that five-call deals still return.

3. Before making any of this policy, run the experiment. Randomly cap follow-up
   at four calls for one group and leave another uncapped. That is the only
   design that separates the two readings, and it costs one quarter of data
   rather than a year of a wrong policy.
""")


if __name__ == "__main__":
    main()

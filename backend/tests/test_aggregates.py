"""
The dashboard's numbers, checked against pandas on the source CSV.

This is the test that would have caught the 1,000-row truncation from the other
side: the aggregate functions are fed all 3,500 rows and their output has to
match an independent calculation, not merely look reasonable.
"""
import pandas as pd

from app.main import _budget, _followup, _summary


def test_summary_matches_pandas(rows, csv):
    s = _summary(rows)
    assert s["total"] == 3500
    assert s["avgLtvMonths"] == round(csv["ltv_months"].mean(), 1)
    assert s["avgProfit"] == round(csv["cumulative_profit"].mean(), 1)
    assert s["purchaseRate"] == round(100 * csv["purchased"].mean(), 1)
    assert s["upsellRate"] == round(100 * csv["upsell"].mean(), 1)
    assert s["missing"] == {"ltvMonths": 4, "cumulativeProfit": 29}


def test_summary_tiers_match_pandas(rows, csv):
    tier = pd.cut(csv["ad_budget"], [-1, 1500, 5000, 10 ** 9],
                  labels=["Low", "Mid", "High"])
    expected = {t: int((tier == t).sum()) for t in ("Low", "Mid", "High")}
    assert {t["tier"]: t["count"] for t in _summary(rows)["tiers"]} == expected


def test_budget_pot_is_real_spend_not_a_round_number(rows, csv):
    """
    The pot was once an invented 1,000,000 presented as though it were a
    finding. It has to stay tied to the data.
    """
    b = _budget(rows)
    assert b["pot"] == int(csv["ad_budget"].sum()) == 16_293_700


def test_budget_curve_is_non_monotonic(rows):
    """The central Package 6 claim: return peaks in the middle and falls off."""
    curve = {c["budget"]: c["returnPerShekel"] for c in _budget(rows)["curve"]}
    assert curve[2000] > curve[1500]
    assert curve[2000] > curve[20000]
    assert all(curve[b] < 1.0 for b in (6000, 8000, 10000, 15000, 20000))


def test_every_campaign_is_indexed_for_the_picker(rows):
    """
    The picker once offered ten of 3,500 because it was wired to a sample
    endpoint. Nothing failed; the product just showed 0.3% of the data.
    """
    b = _budget(rows)
    assert sum(c["campaigns"] for c in b["curve"]) == 3471   # minus 29 null profits


def test_followup_gap_survives_controlling_for_quality(rows):
    f = _followup(rows)
    assert f["gapRaw"] > 6.0
    assert f["gapWithinQuality"] > 6.0
    assert f["explainedByQualityPct"] < 20, "quality should explain very little"


def test_followup_funnel_totals_match_pandas(rows, csv):
    f = _followup(rows)
    assert f["funnel"][0]["count"] == int(csv["num_leads"].sum()) == 161_772
    assert f["unanswered"] == int((csv["num_leads"] - csv["leads_answered"]).sum())
    assert f["campaigns"] == 3500

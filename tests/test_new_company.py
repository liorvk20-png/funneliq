"""
The state every paying customer passes through: a workspace with no data in it.

Until this file existed, `/api/insights` raised on an empty table — max() of an
empty sequence in the budget curve, and None/None in the follow-up ratio. That
is the first screen a company sees after signing up, so the crash was on the
one path guaranteed to be taken by every new customer and by no existing test,
all of which fed the full 3,500-row CSV.

The partial cases matter for the same reason. A first upload of a handful of
campaigns can easily contain no closed deals, or no profit figures yet, and
each of those divides by zero somewhere different.
"""
import json

import pytest

from app.main import _budget, _followup, _summary

AGGREGATES = (_summary, _budget, _followup)


def campaign(i, *, budget=2000, closed=3, calls=2, profit=4000.0, ltv=6.0):
    return {
        "id": i, "ad_budget": budget, "budget_tier": "Mid",
        "num_leads": 40, "leads_answered": 25,
        "ltv_months": ltv, "cumulative_profit": profit,
        "customer_acquisition_cost": 90,
        "purchased": True, "upsell": False, "referred": False,
        "closed": closed, "not_closed": 5,
        "calls_to_closed": calls, "calls_to_not_closed": 3,
        "followup_1": 20, "followup_2": 15, "followup_3": 9,
        "followup_4": 4, "followup_5": 1,
    }


CASES = {
    "brand new company": [],
    "a single campaign": [campaign(1)],
    "nothing closed yet": [campaign(1, closed=0, calls=0),
                           campaign(2, closed=0, calls=0, budget=3000)],
    "no profit recorded": [campaign(1, profit=None), campaign(2, profit=None, calls=6)],
    "fast closers only": [campaign(i, calls=1) for i in range(1, 6)],
    "everything broke even": [campaign(i, profit=0.0) for i in range(1, 4)],
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("fn", AGGREGATES, ids=lambda f: f.__name__)
def test_aggregate_survives(fn, name):
    """No exception, and a body the browser can actually parse."""
    json.dumps(fn(CASES[name]))


def test_empty_company_reports_zero_rather_than_inventing_numbers():
    s = _summary([])
    assert s["total"] == 0
    # None means "we have nothing to average", which the dashboard renders as
    # "אין נתון". A 0 here would be a claim that the average LTV is zero.
    assert s["avgLtvMonths"] is None and s["avgProfit"] is None


def test_empty_company_offers_no_budget_recommendation():
    b = _budget([])
    assert b["curve"] == [] and b["strategies"] == []
    assert b["best"] is None and b["gainPct"] is None


def test_followup_gap_needs_both_ends_of_the_range():
    """
    With only fast closers there is nothing to compare against. The ratio has to
    be absent, not computed from one group and presented as a comparison.
    """
    f = _followup(CASES["fast closers only"])
    assert f["gapRaw"] is None
    assert f["explainedByQualityPct"] is None

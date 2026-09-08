"""
Mapping Meta Ads insights into the same shape a CSV upload produces.

fetch_campaign_insights touches the network and is not tested here (there is
nothing to assert against without a live account); map_to_funnel_frame is
pure and is the part that decides what a company actually sees, so it is
tested against hand-built Meta API responses. The output is checked against
app.ingest.inspect() directly, the same gate a real upload goes through --
if a Meta-sourced frame ever failed that gate, that would be a bug here, not
a reason to special-case ingest.py.
"""
import pandas as pd

from app.ingest import BOOLEAN_COLUMNS, INTEGER_COLUMNS, NULLABLE_COLUMNS, inspect
from app.integrations.meta_ads import _leads_from_actions, map_to_funnel_frame


def test_leads_are_counted_from_either_action_type():
    actions = [
        {"action_type": "link_click", "value": "40"},
        {"action_type": "onsite_conversion.lead_grouped", "value": "12"},
    ]
    assert _leads_from_actions(actions) == 12
    assert _leads_from_actions([{"action_type": "lead", "value": "7"}]) == 7
    assert _leads_from_actions(None) == 0
    assert _leads_from_actions([]) == 0


def test_a_garbled_action_value_is_skipped_not_fatal():
    actions = [{"action_type": "lead", "value": "not-a-number"},
              {"action_type": "lead", "value": "3"}]
    assert _leads_from_actions(actions) == 3


def one_campaign(spend=1000, leads=20, name="Campaign A"):
    return {
        "campaign_id": "123", "campaign_name": name, "spend": str(spend),
        "actions": [{"action_type": "onsite_conversion.lead_grouped", "value": str(leads)}],
    }


def test_the_frame_has_every_column_ingest_requires():
    frame, names = map_to_funnel_frame([one_campaign()])
    assert names == ["Campaign A"]
    for col in INTEGER_COLUMNS + BOOLEAN_COLUMNS + NULLABLE_COLUMNS:
        assert col in frame.columns


def test_ad_spend_and_leads_come_from_meta_the_rest_starts_unknown():
    frame, _ = map_to_funnel_frame([one_campaign(spend=1000, leads=20)])
    row = frame.iloc[0]
    assert row["ad_budget"] == 1000
    assert row["num_leads"] == 20
    # Known from the ad platform alone: spend / leads.
    assert row["customer_acquisition_cost"] == 50
    # Not yet knowable from Meta -- honestly zero/unknown, not guessed.
    assert row["leads_answered"] == 0
    assert row["leads_not_answered"] == 20
    assert row["closed"] == 0 and row["not_closed"] == 0
    assert not row["purchased"] and not row["upsell"] and not row["referred"]
    assert pd.isna(row["ltv_months"]) and pd.isna(row["cumulative_profit"])


def test_a_lead_free_campaign_does_not_divide_by_zero():
    frame, _ = map_to_funnel_frame([one_campaign(spend=500, leads=0)])
    assert frame.iloc[0]["customer_acquisition_cost"] == 0


def test_the_mapped_frame_passes_the_same_gate_a_csv_does():
    frame, names = map_to_funnel_frame(
        [one_campaign(name="A"), one_campaign(name="B", spend=2000, leads=40)])
    report, clean = inspect(frame)
    assert report.ok, report.errors
    assert clean is not None and len(clean) == len(names) == 2
    # leads_answered + leads_not_answered == num_leads by construction, so the
    # file's own arithmetic check does not fire on a row nobody has edited yet.
    assert not any("ענו" in w for w in report.warnings)

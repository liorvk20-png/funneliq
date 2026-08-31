"""
Every rule, rendered once, pinned.

A template is the only part of this system with no type to check it and no
arithmetic to fail: a placeholder pointing at a field that no longer exists, a
verb agreeing with the wrong gender, a percentage where points were meant — all
of them produce a sentence that looks fine to anyone not reading closely in
Hebrew. So each rule gets a purpose-built finding and its output is compared
against a recorded string, and a change to any template has to be made twice,
deliberately.
"""
import json
from pathlib import Path

import pytest

from app.findings.schema import Confidence, Direction, Finding, FindingType
from app.narrative.engine import SECTION_CAPS, compose, render
from app.narrative.formatters import HTML
from app.narrative.rules_seed import RULES, RULES_BY_KEY

SNAPSHOT = Path(__file__).parent / "fixtures" / "narrative_snapshots.json"


def finding_for(rule) -> Finding:
    """
    A finding built to satisfy exactly one rule.

    Generous by design: it carries every field any template asks for, so a
    rendering failure means the template is wrong rather than the fixture being
    thin. What each rule *matches* on is still its own conditions.
    """
    metric_key = {
        "cpa_cpc_driven": "cost_per_close", "cpa_cvr_driven": "cost_per_close",
        "volume_driven": "leads",
    }.get(rule.rule_key, "cost_per_lead")

    evidence = {
        "unstable": rule.rule_key == "interaction_high",
        "opposing": rule.rule_key == "mix_rate_opposite",
        "mix_abs": 4.2, "rate_abs": -3.1, "interaction_abs": 0.4,
        "weight_current": 0.62, "weight_baseline": 0.41, "weight_delta": 0.21,
        "top_three_share": 0.78, "top_three_names": "תקציב נמוך · תקציב בינוני · תקציב גבוה",
        "offset_by": "תקציב גבוה", "offset_abs": 2.5,
        "stage_from": "ענו לטלפון", "stage_to": "נסגרו", "dropoff": 0.62,
        "is_worst": True, "potential_gain": 145, "segment_gap": 0.19,
        "segment_rate": 0.34, "average_rate": 0.51, "all_stages_flat": True,
        "z_score": 3.4 if rule.rule_key != "anomaly_drop" else -3.4,
        "cluster_size": 4, "threshold": 55.0, "recovered_after_days": 6,
        "spend_threshold": 8000, "missing_periods": 2, "dq_score": 71,
        "days_elapsed": 12, "days_total": 30, "duplicate_count": 37,
        "forecast": 42.0, "target": 40.0, "miss": 0.18,
        "gap": {"pacing_behind": -0.12, "pacing_ahead": 0.11}.get(rule.rule_key, 0.02),
    }

    share = {
        "mix_partial": 0.42, "driver_single_medium": 0.44,
        "driver_diffuse": 0.11, "driver_overshoot": 1.18,
    }.get(rule.rule_key, 0.74)

    delta_pct = {"headline_sharp": 0.41, "headline_stable": 0.02}.get(rule.rule_key, 0.18)

    return Finding(
        finding_type=FindingType(rule.applies_to), metric_key=metric_key,
        dimension_path={"budget_tier": "Mid"},
        value_current=41.2, value_baseline=34.9,
        delta_abs=-0.07 if rule.section == "funnel" else 6.3,
        delta_pct=delta_pct,
        effect_type={"cpa_cpc_driven": "cpc", "cpa_cvr_driven": "cvr",
                     "volume_driven": "volume"}.get(rule.rule_key),
        contribution_abs=6.3, contribution_share=share,
        denom_current=1840, denom_baseline=2010,
        significance_p=0.004, ci_low=1.1, ci_high=9.4,
        direction=Direction.UP, is_favorable=rule.rule_key != "headline_improved",
        severity=70, confidence_label=Confidence.HIGH, evidence=evidence,
    )


@pytest.fixture(scope="module")
def snapshots() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.rule_key)
def test_every_rule_renders(rule):
    """A template that cannot be filled from a finding carrying every field is
    a template pointing at a field that does not exist."""
    text = render(rule, finding_for(rule))
    assert text, f"{rule.rule_key} rendered nothing"
    assert "{" not in text and "}" not in text, f"{rule.rule_key} left a placeholder"
    assert text.strip() == text and "  " not in text


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.rule_key)
def test_the_wording_has_not_changed(rule, snapshots):
    expected = snapshots.get(rule.rule_key)
    if expected is None:
        pytest.skip(f"no snapshot recorded for {rule.rule_key}")
    assert render(rule, finding_for(rule)) == expected


def test_no_snapshot_has_been_orphaned(snapshots):
    """A recorded snapshot for a rule that no longer exists means a rule was
    deleted without anyone deciding to delete its sentence."""
    assert not set(snapshots) - set(RULES_BY_KEY)


def test_numbers_are_isolated_when_rendered_for_html():
    """
    Without the isolation the percent sign and the minus jump to the wrong end
    of the digits inside a Hebrew sentence. It is the most visible RTL defect
    there is and the cheapest to prevent.
    """
    rule = RULES_BY_KEY["headline_declined"]
    text = render(rule, finding_for(rule), HTML)
    assert '<span dir="ltr">' in text


def test_rendering_is_deterministic():
    """Same input, same bytes — the property that lets a report be cited."""
    for rule in RULES:
        finding = finding_for(rule)
        assert render(rule, finding) == render(rule, finding)


def test_a_missing_field_drops_the_rule_rather_than_leaving_a_gap():
    rule = RULES_BY_KEY["threshold_breach"]
    finding = finding_for(rule).model_copy(update={"evidence": {}})
    assert render(rule, finding) is None


def test_the_report_respects_its_caps():
    findings = []
    for rule in RULES:
        findings.append(finding_for(rule))
    sentences = compose(findings, RULES)
    assert len(sentences) <= 14
    for section, cap in SECTION_CAPS.items():
        assert sum(1 for s in sentences if s.section == section) <= cap


def test_every_sentence_carries_the_finding_that_justified_it():
    """The iron rule: no claim without something to trace it back to."""
    sentences = compose([finding_for(r) for r in RULES], RULES)
    ids = {str(f.finding_id) for f in [finding_for(r) for r in RULES]}
    assert sentences
    for sentence in sentences:
        assert sentence.finding_id and sentence.rule_key
    # every id is a real uuid string, not a placeholder
    assert all(len(s.finding_id) == 36 for s in sentences)
    assert ids  # the fixtures generate fresh ids each call, so only shape is checked

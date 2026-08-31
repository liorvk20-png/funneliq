"""
The eight rules the specification calls guardrails, tested as behaviour.

Each of these is a promise the product makes about what it will never say. A
promise enforced by convention is a promise that lasts until the next person
adds a template in a hurry, so each one is enforced in code and pinned here.
"""
import re

import pytest

from app.findings.schema import Confidence, Direction, Finding, FindingType
from app.narrative.conditions import OPERATORS, evaluate
from app.narrative.engine import REPORT_CEILING, compose, match, render
from app.narrative.rules_seed import RULES, RULES_BY_KEY
from tests.test_narrative_snapshots import finding_for

# Wording that asserts one thing made another happen.
CAUSAL = ("נובע", "נובעת", "מוסבר", "מוסברת", "גרם", "בגלל", "מסביר")
# Rules whose whole point is that no cause can be assigned.
DENIES_CAUSATION = {"interaction_high"}


def base(**kw) -> Finding:
    fields = dict(
        finding_type=FindingType.SEGMENT_DRIVER, metric_key="cost_per_lead",
        dimension_path={"budget_tier": "Mid"},
        value_current=40.0, value_baseline=32.0, delta_abs=8.0, delta_pct=0.25,
        contribution_abs=8.0, contribution_share=0.75,
        denom_current=1500, denom_baseline=1600, significance_p=0.001,
        direction=Direction.UP, is_favorable=False, severity=70,
        confidence_label=Confidence.HIGH,
    )
    fields.update(kw)
    return Finding(**fields)


# ── 1 · no sentence without a finding ────────────────────────────────────
def test_a_sentence_can_only_come_from_a_finding():
    sentences = compose([base()], RULES)
    assert sentences and all(s.finding_id for s in sentences)


def test_no_findings_produces_no_text():
    assert compose([], RULES) == []


# ── 2 · no causal wording without the evidence ───────────────────────────
@pytest.mark.parametrize("rule", [r for r in RULES if any(w in r.template_he for w in CAUSAL)],
                         ids=lambda r: r.rule_key)
def test_causal_wording_requires_high_confidence_and_a_dominant_share(rule):
    """
    A template that claims causation must be unable to fire without both
    halves of the licence. Checked on the rule's own conditions rather than on
    a sample finding, so a rule that has simply not been exercised yet cannot
    pass by luck.
    """
    fields = {c.get("field") for c in rule.conditions}
    has_confidence = "confidence_label" in fields
    has_share = "contribution_share" in fields
    # A rule that denies causation contains the vocabulary and makes no claim.
    if rule.rule_key in DENIES_CAUSATION:
        return
    assert has_confidence and has_share, (
        f"{rule.rule_key} uses causal wording without gating on both "
        "confidence_label and contribution_share")


def test_a_medium_confidence_finding_gets_correlational_wording():
    finding = base(finding_type=FindingType.MIX_SHIFT, effect_type="mix",
                   confidence_label=Confidence.MEDIUM,
                   evidence={"unstable": False})
    rule = match(RULES, finding)
    assert rule is not None
    assert rule.rule_key.endswith("_soft")
    assert "נובע" not in render(rule, finding)


def test_the_same_finding_at_high_confidence_gets_the_causal_wording():
    finding = base(finding_type=FindingType.MIX_SHIFT, effect_type="mix",
                   confidence_label=Confidence.HIGH, evidence={"unstable": False})
    assert match(RULES, finding).rule_key == "mix_dominant"


def test_an_unstable_decomposition_cannot_be_attributed_to_either_force():
    """
    Both rules used to fire in the same report: one attributing the change to
    the mix, the other saying it could not be attributed. Three lines apart.
    """
    finding = base(finding_type=FindingType.MIX_SHIFT, effect_type="mix",
                   confidence_label=Confidence.HIGH, evidence={"unstable": True})
    assert match(RULES, finding).rule_key == "interaction_high"


# ── 3 · no invented numbers ──────────────────────────────────────────────
def test_a_template_never_renders_an_empty_placeholder():
    for rule in RULES:
        text = render(rule, finding_for(rule))
        assert text is None or not re.search(r"\{|\}|ב־\s*\.|\(\s*\)", text)


def test_every_placeholder_names_a_field_that_exists():
    """A typo in a placeholder silently deletes the rule at runtime. Here it
    fails the build instead."""
    for rule in RULES:
        assert render(rule, finding_for(rule)) is not None, rule.rule_key


# ── 4 · length ───────────────────────────────────────────────────────────
def test_the_report_cannot_exceed_its_ceiling():
    many = [finding_for(r).model_copy(update={"severity": 90}) for r in RULES] * 3
    assert len(compose(many, RULES)) <= REPORT_CEILING


# ── 5 · determinism ──────────────────────────────────────────────────────
def test_the_same_findings_produce_the_same_report_byte_for_byte():
    findings = [finding_for(r) for r in RULES]
    first = [(s.section, s.rule_key, s.text_he) for s in compose(findings, RULES)]
    second = [(s.section, s.rule_key, s.text_he) for s in compose(findings, RULES)]
    assert first == second


def test_ordering_does_not_depend_on_the_order_findings_arrive_in():
    findings = [finding_for(r) for r in RULES]
    forward = [s.text_he for s in compose(findings, RULES)]
    backward = [s.text_he for s in compose(list(reversed(findings)), RULES)]
    assert forward == backward


def test_no_template_reads_a_clock_or_a_random_source():
    import inspect

    from app.narrative import engine, formatters
    for module in (engine, formatters):
        source = inspect.getsource(module)
        assert "random" not in source
        assert "datetime.now" not in source and "time.time" not in source


# ── 7 · no personal data in the text ─────────────────────────────────────
@pytest.mark.parametrize("value", [
    "dana@example.com", "+972-52-555-1234", "0525551234",
    "3f2a9c11-4b7e-4c2a-8f11-2e9d5a6b7c80", "10029384",
])
def test_a_dimension_value_shaped_like_an_identifier_is_redacted(value):
    """
    Dimension values come from a customer's own file. A column the matcher
    placed as a dimension could hold an address or a record id, and a report is
    read by more people than the file was.

    This replaced a blocklist of words, which flagged the funnel stage "ענו
    לטלפון" as a phone number — a check on vocabulary rather than on shape.
    """
    from app.narrative.hebrew import REDACTED, safe_value
    assert safe_value("channel", value) == REDACTED


@pytest.mark.parametrize("value", ["Meta", "mobile", "תקציב בינוני", "Q3 campaign"])
def test_an_ordinary_segment_name_is_left_alone(value):
    from app.narrative.hebrew import REDACTED, safe_value
    assert safe_value("channel", value) != REDACTED


def test_dimension_values_are_translated_rather_than_echoed():
    """
    Dimension values reach the reader as words. An untranslated value is
    acceptable; an arbitrary string from a customer's file rendered as though
    it were one of our own labels is how raw data gets into a report.
    """
    from app.narrative.hebrew import dimension_phrase
    assert dimension_phrase({"budget_tier": "Mid"}) == "תקציב בינוני"
    assert dimension_phrase({"budget_tier": "whatever"}) == "whatever"


# ── the DSL stays closed ─────────────────────────────────────────────────
def test_the_condition_language_has_no_escape_hatch():
    """
    Rules are data, and data may one day be edited by someone who is not us.
    Nine comparisons against literals, and nothing that executes.
    """
    assert set(OPERATORS) == {"eq", "neq", "gt", "gte", "lt", "lte",
                              "in", "between", "abs_gte", "exists"}


def test_an_unknown_operator_stops_matching_rather_than_the_report():
    assert evaluate([{"field": "delta_pct", "op": "arbitrary_code", "value": 1}], base()) is False


def test_rule_keys_are_unique():
    assert len(RULES_BY_KEY) == len(RULES)

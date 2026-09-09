"""
The metrics this product knows how to talk about.

Each carries its Hebrew grammatical gender, its unit, and which direction is
good news — the three things a number cannot tell you about itself and without
which no correct sentence can be built from it.

Labels are chosen to be grammatically singular. Hebrew agrees a verb with
number as well as gender, and the schema carries only gender, so "מספר הלידים"
(masculine singular) is used rather than "לידים" (plural), which would need
"גדלו" and would come out wrong.
"""
from __future__ import annotations

from app.findings.schema import (
    Direction,
    Gender,
    MetricDefinition,
    MetricType,
    Unit,
    VerbFamily,
)


def _m(key, he, en, gender, mtype, unit, good, **kw) -> MetricDefinition:
    # A sum or a count grows; a rate or a price rises. Defaulted from the metric
    # type so a new metric gets the right pair without anyone remembering to
    # think about it, and overridable where the default reads wrong.
    kw.setdefault("verb_family",
                  VerbFamily.GROW_SHRINK if mtype == MetricType.ADDITIVE
                  else VerbFamily.RISE_FALL)
    return MetricDefinition(
        metric_key=key, label_he=he, label_en=en, gender_he=gender,
        metric_type=mtype, unit=unit, direction_good=good, **kw)


METRICS: dict[str, MetricDefinition] = {m.metric_key: m for m in [
    # ---- additive totals -------------------------------------------------
    _m("spend", "סך התקציב", "Ad spend", Gender.M,
       MetricType.ADDITIVE, Unit.CURRENCY, Direction.DOWN),
    _m("leads", "מספר הלידים", "Leads", Gender.M,
       MetricType.ADDITIVE, Unit.COUNT, Direction.UP),
    _m("closed", "מספר הסגירות", "Closed deals", Gender.M,
       MetricType.ADDITIVE, Unit.COUNT, Direction.UP),
    _m("profit", "הרווח", "Profit", Gender.M,
       MetricType.ADDITIVE, Unit.CURRENCY, Direction.UP),

    # ---- rates: a proportion of a population -----------------------------
    _m("answer_rate", "שיעור המענה", "Answer rate", Gender.M,
       MetricType.RATE, Unit.PERCENT, Direction.UP,
       numerator_key="leads_answered", denominator_key="num_leads"),
    _m("close_rate", "שיעור הסגירה", "Close rate", Gender.M,
       MetricType.RATE, Unit.PERCENT, Direction.UP,
       numerator_key="closed", denominator_key="leads_answered"),
    _m("purchase_rate", "שיעור הרכישה", "Purchase rate", Gender.M,
       MetricType.RATE, Unit.PERCENT, Direction.UP,
       numerator_key="purchased", denominator_key="campaigns"),
    _m("upsell_rate", "שיעור המכירה הנוספת", "Upsell rate", Gender.M,
       MetricType.RATE, Unit.PERCENT, Direction.UP,
       numerator_key="upsell", denominator_key="campaigns"),
    _m("referral_rate", "שיעור ההפניות", "Referral rate", Gender.M,
       MetricType.RATE, Unit.PERCENT, Direction.UP,
       numerator_key="referred", denominator_key="campaigns"),

    # ---- ratios: one total over another ----------------------------------
    # These are the feminine ones, and the reason the gender field exists:
    # "עלות" takes עלתה where "שיעור" takes עלה.
    _m("cost_per_lead", "העלות לליד", "Cost per lead", Gender.F,
       MetricType.RATIO, Unit.CURRENCY, Direction.DOWN,
       numerator_key="spend", denominator_key="leads"),
    _m("cost_per_close", "העלות לסגירה", "Cost per closed deal", Gender.F,
       MetricType.RATIO, Unit.CURRENCY, Direction.DOWN,
       numerator_key="spend", denominator_key="closed"),
    _m("acquisition_cost", "עלות גיוס הלקוח", "Customer acquisition cost", Gender.F,
       MetricType.RATIO, Unit.CURRENCY, Direction.DOWN),
    _m("return_per_shekel", "התשואה לשקל", "Return per shekel", Gender.F,
       MetricType.RATIO, Unit.RATIO, Direction.UP,
       numerator_key="profit", denominator_key="spend"),
    _m("ltv_months", "שווי הלקוח", "Customer lifetime", Gender.M,
       MetricType.RATIO, Unit.MONTHS, Direction.UP),
]}


def metric(key: str) -> MetricDefinition:
    if key not in METRICS:
        raise KeyError(
            f"Unknown metric {key!r}. Every finding names a metric, and every "
            "metric needs a label, a gender and a direction before it can be "
            "written about."
        )
    return METRICS[key]


def is_favorable(metric_key: str, delta: float) -> bool | None:
    """
    Whether a movement is good news for this metric.

    Cost falling and revenue falling are the same sign and opposite news, which
    is why direction_good exists and why no caller should ever infer this from
    the number.
    """
    if delta == 0:
        return None
    definition = metric(metric_key)
    rising = delta > 0
    return rising == (definition.direction_good == Direction.UP)

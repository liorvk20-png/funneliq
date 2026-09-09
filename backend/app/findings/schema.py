"""
The Finding — the contract everything else in this work package is built on.

Nothing goes from a number straight to a sentence. Metrics become findings, and
only findings become words. That indirection is the whole design: the same
objects feed the dashboard, the alert engine, the PDF and, eventually, a
conversational agent, and each of those reads structure rather than re-deriving
meaning from raw rows. Adding the fourth consumer then costs nothing, because
it consumes what already exists.

The iron rule that follows: no sentence exists without a finding_id behind it.
Every claim the product makes can be traced back to the object that justified
it, and a claim that cannot is a bug rather than a wording choice.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class FindingType(StrEnum):
    METRIC_CHANGE = "metric_change"
    MIX_SHIFT = "mix_shift"
    RATE_SHIFT = "rate_shift"
    SEGMENT_DRIVER = "segment_driver"
    FUNNEL_DROPOFF = "funnel_dropoff"
    ANOMALY = "anomaly"
    THRESHOLD_BREACH = "threshold_breach"
    PACING_RISK = "pacing_risk"
    SATURATION_POINT = "saturation_point"
    COHORT_DECAY = "cohort_decay"
    NEW_SEGMENT = "new_segment"
    DISAPPEARED_SEGMENT = "disappeared_segment"
    DATA_QUALITY = "data_quality"
    SMALL_SAMPLE = "small_sample"
    FORECAST_MISS = "forecast_miss"
    FACTOR_SHIFT = "factor_shift"


class Gender(StrEnum):
    """
    Grammatical gender of the metric's Hebrew name.

    Not decoration. A Hebrew verb agrees with its subject, so the same event
    is "עלתה" for עלות and "עלה" for שיעור. Without this field every second
    sentence is wrong in a way any Hebrew reader notices in the first line —
    and a report whose grammar is broken does not get read as authoritative
    however sound the arithmetic under it is.
    """
    M = "m"
    F = "f"


class VerbFamily(StrEnum):
    """
    Which pair of Hebrew verbs the metric takes.

    Gender alone is not enough, and the specification's own table shows why:
    עלות goes עלתה/ירדה while רווח goes גדל/קטן. Prices and rates rise and
    fall; quantities and amounts grow and shrink. Using one pair for both
    produces sentences that parse but read as machine output — "הרווח עלה"
    is not wrong so much as not something a person would write.
    """
    RISE_FALL = "rise_fall"      # עלה/ירד  — rates, prices, ratios
    GROW_SHRINK = "grow_shrink"  # גדל/קטן  — counts, sums, amounts


class MetricType(StrEnum):
    ADDITIVE = "additive"     # sums: spend, leads, profit
    RATIO = "ratio"           # one total over another: cost per lead
    RATE = "rate"             # a proportion of a population: answer rate


class Unit(StrEnum):
    CURRENCY = "currency"
    PERCENT = "percent"
    COUNT = "count"
    DAYS = "days"
    MONTHS = "months"
    RATIO = "ratio"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class MetricDefinition(BaseModel):
    metric_key: str
    label_he: str
    label_en: str
    gender_he: Gender
    metric_type: MetricType
    unit: Unit
    decimals: int = 2
    # Which way is good news. Cost per lead going down is an improvement; the
    # number alone cannot say so, and every "is_favorable" in the system
    # ultimately reads this field.
    direction_good: Direction
    verb_family: VerbFamily = VerbFamily.RISE_FALL
    numerator_key: str | None = None
    denominator_key: str | None = None
    min_denominator: int = 30
    version: int = 1


class AnalysisWindow(BaseModel):
    window_id: UUID = Field(default_factory=uuid4)
    current_start: date
    current_end: date
    baseline_start: date
    baseline_end: date
    baseline_method: str = "previous_period"

    @model_validator(mode="after")
    def periods_must_be_the_same_length(self) -> AnalysisWindow:
        """
        Comparing four weeks against five is the most common way a comparison
        lies, and it lies in the direction of whichever period is longer. It is
        rejected rather than quietly rescaled: a caller who meant to compare
        unequal periods needs to say what they meant by that.
        """
        current = (self.current_end - self.current_start).days
        baseline = (self.baseline_end - self.baseline_start).days
        if current != baseline:
            raise ValueError(
                f"The current period spans {current} days and the baseline {baseline}. "
                "Two periods of different lengths cannot be compared directly."
            )
        if current < 0:
            raise ValueError("A period cannot end before it starts.")
        return self


class Finding(BaseModel):
    """One structured observation. The unit of everything downstream."""
    finding_id: UUID = Field(default_factory=uuid4)
    company_id: UUID | None = None
    run_id: UUID | None = None
    window_id: UUID | None = None

    finding_type: FindingType
    metric_key: str
    dimension_path: dict[str, str] = Field(default_factory=dict)

    value_current: float | None = None
    value_baseline: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None

    effect_type: str | None = None          # mix | rate | interaction | a factor name
    contribution_abs: float | None = None
    contribution_share: float | None = None

    denom_current: float | None = None
    denom_baseline: float | None = None
    significance_p: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None

    direction: Direction | None = None
    is_favorable: bool | None = None
    severity: int = Field(default=0, ge=0, le=100)
    confidence_label: Confidence = Confidence.INSUFFICIENT

    parent_finding_id: UUID | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    @property
    def dimension_depth(self) -> int:
        return len(self.dimension_path)

    @property
    def allows_causal_language(self) -> bool:
        """
        Both halves of the rule, in the one place that owns it.

        Causal phrasing needs the strongest evidence band *and* a driver that
        accounts for most of the movement. Anything else gets correlational
        wording. This is enforced by the engine rather than left to whoever
        writes the next template, because a template author reading a large
        number will reach for "נובע מ" every time.
        """
        return (self.confidence_label == Confidence.HIGH
                and abs(self.contribution_share or 0.0) >= 0.60)

    def field_value(self, name: str) -> Any:
        """
        Read a field by name for the condition DSL and the templates.

        Restricted to the model's own fields plus evidence. A template asking
        for something that does not exist gets None, and the rule is skipped
        and logged rather than rendered with a hole in it.
        """
        # type(self).model_fields, not self.model_fields: reading it from the
        # instance is deprecated in pydantic 2.11 and emitted a warning on
        # every placeholder in every sentence — over a thousand per test run,
        # which is how a real warning goes unnoticed.
        if name in type(self).model_fields:
            return getattr(self, name)
        if name == "dimension_depth":
            return self.dimension_depth
        return self.evidence.get(name)

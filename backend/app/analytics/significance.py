"""
Whether a difference is worth speaking about.

Every gate here exists to stop the same failure: a segment with eleven rows
that moved by forty percent, printed with the same confidence as one with
eleven thousand. Small denominators produce the largest percentage swings, so
without a gate the ranking fills up with exactly the findings least worth
reading, and the product looks most certain where it knows least.

Nothing is discarded for being uncertain. It is labelled `insufficient`,
carried through, and shown in the quality section. A person who reads "too few
records to conclude" trusts the system more afterwards than one who reads a
confident number and later finds out what it rested on.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3, "insufficient": 0.0}

# (minimum denominator, maximum p) for each label, strictest first.
GATES = (
    ("high", 1000, 0.01),
    ("medium", 300, 0.05),
)
LOW_P = 0.10

BOOTSTRAP_SAMPLES = 2000


@dataclass(frozen=True)
class Significance:
    p_value: float | None
    ci_low: float | None
    ci_high: float | None
    label: str

    @property
    def weight(self) -> float:
        return CONFIDENCE_WEIGHT[self.label]

    @property
    def allows_causal_language(self) -> bool:
        """
        Half of the rule. The narrative engine may claim that something is
        *caused by* a driver only when the evidence is strongest and that
        driver accounts for most of the movement; the other half of the test,
        on contribution share, lives with the finding.
        """
        return self.label == "high"


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_proportion_z(
    successes_current: float, trials_current: float,
    successes_baseline: float, trials_baseline: float,
) -> tuple[float | None, float | None, float | None]:
    """
    Two-sided z test for a difference between two rates, with a confidence
    interval on that difference.

    The pooled proportion is used for the test and the unpooled one for the
    interval, which is the conventional pairing: the test asks whether the two
    rates could be the same, so it assumes they are, while the interval
    describes a difference that is not assumed to be zero.
    """
    if trials_current <= 0 or trials_baseline <= 0:
        return None, None, None

    p1 = successes_current / trials_current
    p0 = successes_baseline / trials_baseline
    # This test is only defined for proportions. Handing it a cost ratio —
    # spend over leads, which is happily greater than one — used to reach
    # sqrt() of a negative and crash; the worse version of the same mistake is
    # a ratio just under one, where nothing crashes and the p-value is
    # meaningless. Refusing outright is what makes that impossible.
    if not (0.0 <= p1 <= 1.0 and 0.0 <= p0 <= 1.0):
        raise ValueError(
            f"two_proportion_z received {p1:.4g} and {p0:.4g}. It is defined for "
            "proportions in 0..1; a ratio metric needs bootstrap_ratio instead."
        )
    pooled = (successes_current + successes_baseline) / (trials_current + trials_baseline)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / trials_current + 1 / trials_baseline))

    diff = p1 - p0
    if se_pooled == 0:
        # Both periods are all-success or all-failure. There is no variance to
        # test against; identical rates are not evidence of anything.
        return (None, None, None) if diff == 0 else (0.0, diff, diff)

    z = diff / se_pooled
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    se_unpooled = math.sqrt(
        p1 * (1 - p1) / trials_current + p0 * (1 - p0) / trials_baseline
    )
    return p_value, diff - 1.96 * se_unpooled, diff + 1.96 * se_unpooled


def bootstrap_difference(
    current: list[float], baseline: list[float], *, seed: int = 0,
    samples: int = BOOTSTRAP_SAMPLES,
) -> tuple[float | None, float | None, float | None]:
    """
    Confidence interval on a difference of means, by resampling.

    Used for additive metrics, where a proportion test does not apply and the
    underlying distribution is usually far from normal -- campaign profit in
    particular is heavy-tailed enough that a parametric interval would be
    confidently wrong.

    Resampled as one matrix rather than in a Python loop. The loop version was
    correct and took most of sixteen seconds on a full run, because it does two
    thousand resamples for every segment of every metric; the same arithmetic
    through numpy is the difference between an analysis that runs inside an
    upload and one that needs a queue.

    The seed is fixed. Determinism is a stated requirement here: the same run
    must produce the same text byte for byte, and an interval that wobbled
    between runs would move findings across a confidence gate and silently
    change the report.
    """
    if len(current) < 2 or len(baseline) < 2:
        return None, None, None

    rng = np.random.default_rng(seed)
    a = np.asarray(current, dtype=float)
    b = np.asarray(baseline, dtype=float)
    means_a = a[rng.integers(0, len(a), size=(samples, len(a)))].mean(axis=1)
    means_b = b[rng.integers(0, len(b), size=(samples, len(b)))].mean(axis=1)
    return _interval(means_a - means_b)


def _interval(diffs: np.ndarray) -> tuple[float, float, float]:
    """A 95% interval and the two-sided share of resamples across zero."""
    low, high = np.quantile(diffs, [0.025, 0.975])
    below = float((diffs <= 0).mean())
    return min(1.0, 2 * min(below, 1 - below)), float(low), float(high)


def label(
    denominator_current: float, denominator_baseline: float,
    p_value: float | None, min_denominator: int,
) -> str:
    """
    The confidence band, from the smaller of the two denominators.

    The smaller one decides because that is where the uncertainty lives: a
    segment with 5,000 records this month and 40 last month cannot support a
    confident comparison no matter how solid one side looks.
    """
    denominator = min(denominator_current, denominator_baseline)
    if denominator < min_denominator or p_value is None:
        return "insufficient"
    for name, min_n, max_p in GATES:
        if denominator >= min_n and p_value < max_p:
            return name
    if p_value < LOW_P:
        return "low"
    return "insufficient"


def assess(
    *, denominator_current: float, denominator_baseline: float,
    p_value: float | None, ci_low: float | None = None, ci_high: float | None = None,
    min_denominator: int = 30,
) -> Significance:
    return Significance(
        p_value=p_value, ci_low=ci_low, ci_high=ci_high,
        label=label(denominator_current, denominator_baseline, p_value, min_denominator),
    )


def bootstrap_ratio(
    numerators_current: list[float], denominators_current: list[float],
    numerators_baseline: list[float], denominators_baseline: list[float],
    *, seed: int = 0, samples: int = BOOTSTRAP_SAMPLES,
) -> tuple[float | None, float | None, float | None]:
    """
    Confidence interval on the change in a ratio of two sums.

    Cost per lead is total spend over total leads, not the average of per-row
    costs, and the two are different numbers whenever campaign sizes vary. So
    rows are resampled as pairs and the ratio recomputed from the resampled
    sums -- which also carries the correlation between numerator and
    denominator that a per-row average throws away.
    """
    if len(numerators_current) < 2 or len(numerators_baseline) < 2:
        return None, None, None

    rng = np.random.default_rng(seed)

    def resampled_ratio(nums, dens):
        n = np.asarray(nums, dtype=float)
        d = np.asarray(dens, dtype=float)
        picks = rng.integers(0, len(n), size=(samples, len(n)))
        totals = d[picks].sum(axis=1)
        # A resample that drew no denominator at all has no ratio. Dropping the
        # row is right; substituting a zero would drag the interval toward a
        # value the data never took.
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(totals > 0, n[picks].sum(axis=1) / totals, np.nan)

    diffs = resampled_ratio(numerators_current, denominators_current) - \
        resampled_ratio(numerators_baseline, denominators_baseline)
    diffs = diffs[np.isfinite(diffs)]
    if len(diffs) < samples // 2:
        return None, None, None
    return _interval(diffs)

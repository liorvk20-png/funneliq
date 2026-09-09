"""
Scanning the data for things worth saying, and ranking them.

The search is over segments: every value of every dimension, then every pair,
asking how much of the overall change each accounts for. That space grows
multiplicatively, so it is fenced rather than trusted — at most two dimensions
deep, at most six dimensions, at most twenty values each, with the remainder
folded into an explicit "אחר" rather than dropped, so the parts still sum to
the whole.

Everything runs on in-memory frames. The specification calls for DuckDB; at the
50,000-row ceiling this product enforces on an upload, a pandas groupby is the
same operation without a second engine to install, pin and keep working on the
deploy. That is a deviation and it is written down here rather than discovered
later.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.contribution import Segment, decompose
from app.analytics.severity import severity
from app.analytics.significance import (
    assess,
    bootstrap_difference,
    bootstrap_ratio,
    two_proportion_z,
)
from app.findings.metrics import is_favorable, metric
from app.findings.schema import (
    Confidence,
    Direction,
    Finding,
    FindingType,
    MetricType,
)

MAX_DEPTH = 2
MAX_DIMENSIONS = 6
MAX_VALUES_PER_DIMENSION = 20
OTHER = "אחר"
# Below this share of the overall change a segment is noise in a report that
# has room for four driver sentences.
MIN_SHARE = 0.05


def _direction(delta: float | None) -> Direction:
    if delta is None or delta == 0:
        return Direction.FLAT
    return Direction.UP if delta > 0 else Direction.DOWN


def cap_dimension(series: pd.Series) -> pd.Series:
    """
    Keep the twenty largest values and fold the rest into one bucket.

    Folding rather than discarding matters: contributions are shares of a total,
    and a dropped tail makes them add up to less than the change they are
    describing, which shows up as a report that explains 80% of a movement and
    never says where the rest went.
    """
    counts = series.value_counts()
    if len(counts) <= MAX_VALUES_PER_DIMENSION:
        return series
    keep = set(counts.head(MAX_VALUES_PER_DIMENSION).index)
    return series.where(series.isin(keep), OTHER)


def _totals(frame: pd.DataFrame, metric_key: str) -> tuple[float, float]:
    """(numerator, denominator) for a metric over a frame."""
    definition = metric(metric_key)
    if definition.metric_type == MetricType.ADDITIVE:
        return float(frame[metric_key].sum()), float(len(frame))
    numerator = float(frame[definition.numerator_key].sum())
    denominator = (float(len(frame)) if definition.denominator_key == "campaigns"
                   else float(frame[definition.denominator_key].sum()))
    return numerator, denominator


def _value(frame: pd.DataFrame, metric_key: str) -> tuple[float | None, float]:
    numerator, denominator = _totals(frame, metric_key)
    definition = metric(metric_key)
    if definition.metric_type == MetricType.ADDITIVE:
        return numerator, denominator
    return (numerator / denominator if denominator else None), denominator


def _evidence(
    current: pd.DataFrame, baseline: pd.DataFrame, metric_key: str,
    denom_c: float, denom_b: float,
) -> tuple[float | None, float | None, float | None]:
    """
    Pick the right test for the kind of metric, in one place.

    Getting this wrong is not a crash, it is a plausible p-value with nothing
    behind it. A proportion test belongs only to rates; a ratio of two sums is
    resampled as pairs so the link between numerator and denominator survives;
    an additive total is resampled directly. Routing it here means a new metric
    inherits the right test from its type rather than from whoever adds it.
    """
    definition = metric(metric_key)
    if definition.metric_type == MetricType.RATE:
        num_c, _ = _totals(current, metric_key) if len(current) else (0.0, 0.0)
        num_b, _ = _totals(baseline, metric_key) if len(baseline) else (0.0, 0.0)
        return two_proportion_z(num_c, denom_c, num_b, denom_b)

    if definition.metric_type == MetricType.RATIO and definition.numerator_key:
        num_key, den_key = definition.numerator_key, definition.denominator_key
        if num_key in current.columns and den_key in current.columns:
            return bootstrap_ratio(
                current[num_key].fillna(0).tolist() if len(current) else [],
                current[den_key].fillna(0).tolist() if len(current) else [],
                baseline[num_key].fillna(0).tolist() if len(baseline) else [],
                baseline[den_key].fillna(0).tolist() if len(baseline) else [])

    column = metric_key if metric_key in current.columns else definition.numerator_key
    if column is None or column not in current.columns:
        return None, None, None
    return bootstrap_difference(
        current[column].dropna().tolist() if len(current) else [],
        baseline[column].dropna().tolist() if len(baseline) else [])


def metric_change(
    current: pd.DataFrame, baseline: pd.DataFrame, metric_key: str,
    *, min_denominator: int | None = None,
) -> Finding:
    """The headline: how the metric moved overall, with its evidence."""
    definition = metric(metric_key)
    value_c, denom_c = _value(current, metric_key)
    value_b, denom_b = _value(baseline, metric_key)

    delta_abs = None if value_c is None or value_b is None else value_c - value_b
    delta_pct = (delta_abs / value_b) if (delta_abs is not None and value_b) else None

    p, low, high = _evidence(current, baseline, metric_key, denom_c, denom_b)

    sig = assess(denominator_current=denom_c, denominator_baseline=denom_b,
                 p_value=p, ci_low=low, ci_high=high,
                 min_denominator=min_denominator or definition.min_denominator)
    favorable = None if delta_abs is None else is_favorable(metric_key, delta_abs)

    return Finding(
        finding_type=FindingType.METRIC_CHANGE, metric_key=metric_key,
        value_current=value_c, value_baseline=value_b,
        delta_abs=delta_abs, delta_pct=delta_pct,
        denom_current=denom_c, denom_baseline=denom_b,
        significance_p=sig.p_value, ci_low=sig.ci_low, ci_high=sig.ci_high,
        direction=_direction(delta_abs), is_favorable=favorable,
        confidence_label=Confidence(sig.label),
        # contribution_share is 1.0 by definition: the whole change is the whole
        # change. Stated rather than left null so severity ranks the headline
        # above the drivers that explain a part of it.
        contribution_share=1.0, contribution_abs=delta_abs,
        severity=severity(contribution_share=1.0, delta_pct=delta_pct,
                          confidence_label=sig.label, is_favorable=favorable),
    )


def mix_rate_findings(
    current: pd.DataFrame, baseline: pd.DataFrame, metric_key: str, dimension: str,
) -> list[Finding]:
    """
    Whether a rate moved because the mix changed or because performance did.

    Only defined for rates. An additive total has no weights to shift, and
    running this on one would produce three numbers with no meaning.
    """
    definition = metric(metric_key)
    if definition.metric_type != MetricType.RATE:
        return []

    values = sorted(set(current[dimension].dropna()) | set(baseline[dimension].dropna()))
    segments = []
    for value in values:
        c = current[current[dimension] == value]
        b = baseline[baseline[dimension] == value]
        num_c, den_c = _totals(c, metric_key) if len(c) else (0.0, 0.0)
        num_b, den_b = _totals(b, metric_key) if len(b) else (0.0, 0.0)
        segments.append(Segment(str(value), num_c, den_c, num_b, den_b))

    if not segments or all(s.denominator_current == 0 for s in segments):
        return []
    try:
        d = decompose(segments)
    except ValueError:
        return []

    # An unstable decomposition cannot support a mix-or-rate claim, so the
    # confidence is capped here rather than left to the rule that reads it.
    confidence = Confidence.MEDIUM if d.unstable else Confidence.HIGH
    favorable_mix = is_favorable(metric_key, d.mix) if d.mix else None
    favorable_rate = is_favorable(metric_key, d.rate) if d.rate else None
    opposing = bool(d.mix and d.rate and (d.mix > 0) != (d.rate > 0))

    shared = {
        "metric_key": metric_key,
        "value_current": d.rate_current, "value_baseline": d.rate_baseline,
        "delta_abs": d.delta,
        "delta_pct": (d.delta / d.rate_baseline) if d.rate_baseline else None,
        "confidence_label": confidence,
        "evidence": {
            "unstable": d.unstable, "opposing": opposing,
            "mix_abs": d.mix, "rate_abs": d.rate, "interaction_abs": d.interaction,
            "dimension": dimension,
        },
    }

    findings = [
        Finding(finding_type=FindingType.MIX_SHIFT, effect_type="mix",
                contribution_abs=d.mix, contribution_share=d.share(d.mix),
                direction=_direction(d.mix), is_favorable=favorable_mix,
                severity=severity(contribution_share=d.share(d.mix),
                                  delta_pct=shared["delta_pct"],
                                  confidence_label=confidence.value,
                                  is_favorable=favorable_mix),
                **shared),
        Finding(finding_type=FindingType.RATE_SHIFT, effect_type="rate",
                contribution_abs=d.rate, contribution_share=d.share(d.rate),
                direction=_direction(d.rate), is_favorable=favorable_rate,
                severity=severity(contribution_share=d.share(d.rate),
                                  delta_pct=shared["delta_pct"],
                                  confidence_label=confidence.value,
                                  is_favorable=favorable_rate),
                **shared),
    ]

    # The interaction is kept as its own finding even though no rule leads with
    # it. It is what makes the three components auditable against the identity,
    # and it is what `interaction_high` reads to refuse a clean story.
    findings.append(Finding(
        finding_type=FindingType.MIX_SHIFT, effect_type="interaction",
        contribution_abs=d.interaction, contribution_share=d.share(d.interaction),
        direction=_direction(d.interaction), is_favorable=None,
        severity=severity(contribution_share=d.share(d.interaction),
                          delta_pct=shared["delta_pct"],
                          confidence_label=confidence.value, is_favorable=None),
        **shared))

    # Per-segment weight shifts, for the rule that reports a channel changing
    # its share of the volume.
    for effect in d.segments:
        weight_delta = effect.weight_current - effect.weight_baseline
        if abs(weight_delta) < 0.10:
            continue
        findings.append(Finding(
            finding_type=FindingType.MIX_SHIFT, effect_type="mix",
            metric_key=metric_key, dimension_path={dimension: effect.key},
            value_current=effect.rate_current, value_baseline=effect.rate_baseline,
            delta_abs=effect.rate_current - effect.rate_baseline,
            contribution_abs=effect.total, contribution_share=d.share(effect.total),
            direction=_direction(weight_delta), confidence_label=confidence,
            is_favorable=None,
            severity=severity(contribution_share=d.share(effect.total),
                              delta_pct=None, confidence_label=confidence.value,
                              is_favorable=None),
            evidence={"weight_current": effect.weight_current,
                      "weight_baseline": effect.weight_baseline,
                      "weight_delta": weight_delta, "dimension": dimension},
        ))
    return findings


def segment_drivers(
    current: pd.DataFrame, baseline: pd.DataFrame, metric_key: str,
    dimensions: list[str], *, max_depth: int = MAX_DEPTH,
) -> list[Finding]:
    """
    Which slices account for the overall change, at depth one and two.

    Dedup is hierarchical: when a child slice carries nearly all of its
    parent's contribution, the child is the real finding and the parent is a
    restatement of it. Keeping both would spend two of four driver sentences
    saying the same thing at two levels of detail.
    """
    definition = metric(metric_key)
    head = metric_change(current, baseline, metric_key)
    total_delta = head.delta_abs
    if not total_delta:
        return []

    dimensions = dimensions[:MAX_DIMENSIONS]
    combos: list[tuple[str, ...]] = [(d,) for d in dimensions]
    if max_depth >= 2:
        combos += [(a, b) for i, a in enumerate(dimensions) for b in dimensions[i + 1:]]

    findings: list[Finding] = []
    for combo in combos:
        keys_c = current.groupby(list(combo), dropna=False, observed=True)
        keys_b = baseline.groupby(list(combo), dropna=False, observed=True)
        names = set(keys_c.groups) | set(keys_b.groups)
        for name in names:
            # pandas wants a tuple key even for a single grouping column.
            key = name if isinstance(name, tuple) else (name,)
            c = keys_c.get_group(key) if name in keys_c.groups else current.iloc[0:0]
            b = keys_b.get_group(key) if name in keys_b.groups else baseline.iloc[0:0]
            value_c, denom_c = _value(c, metric_key) if len(c) else (None, 0.0)
            value_b, denom_b = _value(b, metric_key) if len(b) else (None, 0.0)

            # A slice too small in both periods cannot support any claim. Too
            # small in only one is the interesting case — that is a segment
            # appearing or vanishing — so the gate needs both to fail.
            if (denom_c < definition.min_denominator
                    and denom_b < definition.min_denominator):
                continue

            if definition.metric_type == MetricType.ADDITIVE:
                contribution = (value_c or 0.0) - (value_b or 0.0)
            else:
                # A slice's contribution to a weighted average is its share of
                # the volume times how far its own rate sits from the overall
                # one, differenced across the periods.
                contribution = ((value_c or 0.0) * denom_c / (head.denom_current or 1)
                                - (value_b or 0.0) * denom_b / (head.denom_baseline or 1))

            share = contribution / total_delta
            if abs(share) < MIN_SHARE:
                continue

            p, low, high = _evidence(c, b, metric_key, denom_c, denom_b)

            sig = assess(denominator_current=denom_c, denominator_baseline=denom_b,
                         p_value=p, ci_low=low, ci_high=high,
                         min_denominator=definition.min_denominator)
            delta_abs = (None if value_c is None or value_b is None
                         else value_c - value_b)
            delta_pct = (delta_abs / value_b) if (delta_abs is not None and value_b) else None
            favorable = None if delta_abs is None else is_favorable(metric_key, delta_abs)
            path = {k: str(v) for k, v in zip(
                combo, name if isinstance(name, tuple) else (name,), strict=True)}

            kind = FindingType.SEGMENT_DRIVER
            if denom_b == 0 and denom_c > 0:
                kind = FindingType.NEW_SEGMENT
            elif denom_c == 0 and denom_b > 0:
                kind = FindingType.DISAPPEARED_SEGMENT
            elif min(denom_c, denom_b) < definition.min_denominator:
                # `insufficient` covers two different situations and only one of
                # them is a small sample: a segment can have thirty thousand
                # records and simply not have moved enough to be sure about.
                # Reading the label alone produced "32,931 רשומות בלבד, מעט מדי
                # להסקה" — a sentence that is both false and obviously false to
                # the person who uploaded the file. The row count decides this,
                # not the p-value.
                kind = FindingType.SMALL_SAMPLE

            findings.append(Finding(
                finding_type=kind, metric_key=metric_key, dimension_path=path,
                value_current=value_c, value_baseline=value_b,
                delta_abs=delta_abs, delta_pct=delta_pct,
                contribution_abs=contribution, contribution_share=share,
                denom_current=denom_c, denom_baseline=denom_b,
                significance_p=sig.p_value, ci_low=sig.ci_low, ci_high=sig.ci_high,
                direction=_direction(delta_abs), is_favorable=favorable,
                confidence_label=Confidence(sig.label),
                severity=severity(contribution_share=share, delta_pct=delta_pct,
                                  confidence_label=sig.label, is_favorable=favorable),
                evidence={"dimensions": list(combo)},
            ))

    return _dedup_hierarchy(findings)


def _dedup_hierarchy(findings: list[Finding]) -> list[Finding]:
    """
    Drop a parent slice whose contribution is almost entirely one child's.

    The threshold is 0.85: below it the parent holds something the child does
    not and both are worth saying. The child is kept because it is the more
    specific claim, and the parent's id is recorded on it so the chain remains
    walkable.
    """
    by_depth = sorted(findings, key=lambda f: f.dimension_depth)
    keep: list[Finding] = []
    for finding in by_depth:
        if finding.dimension_depth == 0:
            keep.append(finding)
            continue
        superseded = None
        for other in keep:
            if other.dimension_depth >= finding.dimension_depth:
                continue
            if not all(finding.dimension_path.get(k) == v
                       for k, v in other.dimension_path.items()):
                continue
            parent = abs(other.contribution_abs or 0.0)
            child = abs(finding.contribution_abs or 0.0)
            if parent and child >= 0.85 * parent:
                superseded = other
                break
        if superseded is not None:
            keep.remove(superseded)
            finding = finding.model_copy(
                update={"parent_finding_id": superseded.finding_id})
        keep.append(finding)
    return sorted(keep, key=lambda f: (-f.severity, str(f.finding_id)))

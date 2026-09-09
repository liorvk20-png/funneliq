"""
One analysis run, end to end.

Loads two periods, mines findings, writes the narrative, and stores all of it
against a run id so the result can be fetched again unchanged.

The window comes from the uploads table. This product has no per-row date -- a
company uploads a month at a time and `uploads.period` names it -- so "current"
is the newest period and "baseline" is the one before it. That is the
`previous_period` method the specification names, arrived at through the data
that exists rather than through a date column that does not.

A company with one upload has no baseline. It gets absolute values and a
sentence saying why there is no comparison, which is the honest output and also
the one every new customer sees first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from app.analytics.insight_mining import metric_change, mix_rate_findings, segment_drivers
from app.findings.schema import Finding
from app.narrative.engine import Sentence, compose
from app.narrative.rules_seed import RULES

log = logging.getLogger("funneliq.analysis")

# Which metrics lead a report, in the order they are worth reading. Every one is
# computable from the columns the upload gate already guarantees.
HEADLINE_METRICS = ("profit", "return_per_shekel", "cost_per_lead",
                    "answer_rate", "close_rate", "leads")

# The dimensions this data actually has. budget_tier is stored; the volume band
# is derived from ad-side numbers only, so it is known before any outcome and
# cannot smuggle an outcome into a feature. When channel and device arrive from
# the ad platforms, they join this list and nothing else changes.
DIMENSIONS = ("budget_tier", "lead_volume_band")


@dataclass
class AnalysisResult:
    findings: list[Finding] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    current_period: str | None = None
    baseline_period: str | None = None
    current_rows: int = 0
    baseline_rows: int = 0


def prepare(rows: list[dict]) -> pd.DataFrame:
    """
    The stored rows, with the derived columns the engines expect.

    Derivations live here rather than in the miner so that every metric and
    every dimension is computed once from one definition, and a panel cannot
    disagree with a sentence about what "large volume" means.
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for column in ("purchased", "upsell", "referred"):
        if column in df:
            df[column] = df[column].astype("boolean").fillna(False).astype(int)

    df["spend"] = pd.to_numeric(df["ad_budget"], errors="coerce")
    df["leads"] = pd.to_numeric(df["num_leads"], errors="coerce")
    df["profit"] = pd.to_numeric(df.get("cumulative_profit"), errors="coerce")

    if "budget_tier" not in df.columns:
        df["budget_tier"] = df["ad_budget"].map(
            lambda b: "Low" if b <= 1500 else ("Mid" if b <= 5000 else "High"))

    # Thirds of the lead count. Cut on the combined data by the caller so the
    # bands mean the same thing in both periods -- bands recomputed per period
    # would move under the comparison and manufacture a mix shift out of
    # nothing.
    return df


def add_volume_bands(current: pd.DataFrame, baseline: pd.DataFrame) -> None:
    """
    Bands defined once over both periods, in place.

    Computing them separately would put the boundary in a different place each
    month, so a campaign could change band without changing size -- and the mix
    decomposition would report a shift that never happened.
    """
    combined = pd.concat([current["leads"], baseline["leads"]]).dropna()
    if combined.empty:
        for frame in (current, baseline):
            frame["lead_volume_band"] = "medium"
        return
    low, high = combined.quantile([1 / 3, 2 / 3]).tolist()
    if low == high:
        for frame in (current, baseline):
            frame["lead_volume_band"] = "medium"
        return
    for frame in (current, baseline):
        frame["lead_volume_band"] = pd.cut(
            frame["leads"], [-1, low, high, float("inf")],
            labels=["small", "medium", "large"]).astype(str)


def analyse(current_rows: list[dict], baseline_rows: list[dict],
            *, current_period=None, baseline_period=None) -> AnalysisResult:
    """
    Mine one window and write its narrative.

    Findings are produced for every headline metric and, for rates, decomposed
    into mix and rate over each dimension. The narrative engine then picks what
    is worth saying; nothing here decides that, which is what keeps the ranking
    in one place and the wording in another.
    """
    result = AnalysisResult(current_period=current_period, baseline_period=baseline_period)
    current = prepare(current_rows)
    if current.empty:
        return result

    baseline = prepare(baseline_rows)
    result.current_rows, result.baseline_rows = len(current), len(baseline)

    if baseline.empty:
        # No comparison exists. Absolute values only, and a sentence that says
        # so -- rather than a comparison against zero, which would report every
        # metric as having risen infinitely on a company's first month.
        for metric_key in HEADLINE_METRICS:
            try:
                head = metric_change(current, current.iloc[0:0], metric_key)
            except Exception:
                log.exception("metric_change failed for %s", metric_key)
                continue
            head.value_baseline = None
            head.delta_abs = head.delta_pct = None
            result.findings.append(head)
        result.sentences = compose(result.findings, RULES)
        return result

    add_volume_bands(current, baseline)

    for metric_key in HEADLINE_METRICS:
        try:
            result.findings.append(metric_change(current, baseline, metric_key))
        except Exception:
            log.exception("metric_change failed for %s", metric_key)
            continue
        for dimension in DIMENSIONS:
            if dimension not in current.columns:
                continue
            try:
                result.findings += mix_rate_findings(current, baseline, metric_key, dimension)
            except Exception:
                log.exception("mix/rate failed for %s by %s", metric_key, dimension)
        try:
            result.findings += segment_drivers(
                current, baseline, metric_key, list(DIMENSIONS))
        except Exception:
            log.exception("segment drivers failed for %s", metric_key)

    result.sentences = compose(result.findings, RULES)
    return result

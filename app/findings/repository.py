"""
Storing a run so its report can be read again unchanged.

Everything is written through the caller's own token, so the WITH CHECK clauses
added in 007 decide which company a row belongs to. This module proposes a
company_id and the database is free to refuse it -- the same arrangement as
every other write in the product, and the reason a bug here cannot become a
disclosure.
"""
from __future__ import annotations

import logging

from app.findings.schema import Finding
from app.narrative.engine import Sentence

log = logging.getLogger("funneliq.findings")

# Findings are numerous and small. One request per row would make an analysis
# slower to store than to compute.
BATCH = 200


def _row(finding: Finding, company_id: str, run_id: str, window_id: str | None) -> dict:
    return {
        "finding_id": str(finding.finding_id),
        "company_id": company_id, "run_id": run_id, "window_id": window_id,
        "finding_type": finding.finding_type.value,
        "metric_key": finding.metric_key,
        "dimension_path": finding.dimension_path,
        "dimension_depth": finding.dimension_depth,
        "value_current": finding.value_current,
        "value_baseline": finding.value_baseline,
        "delta_abs": finding.delta_abs, "delta_pct": finding.delta_pct,
        "effect_type": finding.effect_type,
        "contribution_abs": finding.contribution_abs,
        "contribution_share": finding.contribution_share,
        "denom_current": finding.denom_current,
        "denom_baseline": finding.denom_baseline,
        "significance_p": finding.significance_p,
        "ci_low": finding.ci_low, "ci_high": finding.ci_high,
        "direction": finding.direction.value if finding.direction else None,
        "is_favorable": finding.is_favorable,
        "severity": finding.severity,
        "confidence_label": finding.confidence_label.value,
        "evidence": finding.evidence,
    }


def save(client, company_id: str, findings: list[Finding], sentences: list[Sentence],
         *, upload_id: str | None = None, window: dict | None = None) -> str | None:
    """
    Persist one run and return its id.

    Findings are written before the narrative because narrative_outputs.finding_id
    is a NOT NULL foreign key -- the iron rule made structural, so a sentence
    with nothing behind it cannot be stored and therefore cannot be shown.

    Only findings a sentence refers to are stored. The miner produces sixty for
    a full run and the report cites six; keeping the rest would fill the table
    with rows nothing reads, and the run can be recomputed from the same data
    at any time because it is deterministic.
    """
    run = client.table("analysis_runs").insert({
        "company_id": company_id, "upload_id": upload_id, "status": "complete",
    }).execute().data[0]
    run_id = run["run_id"]

    window_id = None
    if window:
        stored = client.table("analysis_windows").insert({
            "run_id": run_id, "company_id": company_id, **window,
        }).execute().data
        window_id = stored[0]["window_id"] if stored else None

    cited = {s.finding_id for s in sentences}
    keep = [f for f in findings if str(f.finding_id) in cited]
    rows = [_row(f, company_id, run_id, window_id) for f in keep]
    for i in range(0, len(rows), BATCH):
        client.table("findings").insert(rows[i:i + BATCH]).execute()

    if sentences:
        client.table("narrative_outputs").insert([{
            "company_id": company_id, "run_id": run_id,
            "section": s.section, "ordinal": index,
            "rule_key": s.rule_key, "finding_id": s.finding_id,
            "text_he": s.text_he,
        } for index, s in enumerate(sentences)]).execute()

    return run_id


def latest(client) -> dict:
    """
    The most recent run's narrative and the findings behind it.

    RLS scopes both reads to the caller's company; nothing here filters, which
    is what keeps a forgotten WHERE clause from being a disclosure.
    """
    runs = (client.table("analysis_runs").select("run_id,created_at,upload_id")
            .order("created_at", desc=True).limit(1).execute().data)
    if not runs:
        return {"run": None, "sentences": [], "findings": []}

    run_id = runs[0]["run_id"]
    sentences = (client.table("narrative_outputs")
                 .select("section,ordinal,rule_key,finding_id,text_he")
                 .eq("run_id", run_id).order("ordinal").execute().data)
    findings = (client.table("findings").select("*")
                .eq("run_id", run_id).order("severity", desc=True).execute().data)
    return {"run": runs[0], "sentences": sentences, "findings": findings}

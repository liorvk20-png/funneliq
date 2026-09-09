"""
Findings in, sentences out — and nothing in between that can invent anything.

The pipeline is deliberately dull. Rules are matched against findings by their
declared conditions, the highest-priority match wins, the template is filled
from fields that already exist, and the result is capped. There is no model, no
sampling and no clock: the same run produces the same text byte for byte, which
is what lets a report be cited, compared against last month's, and argued with.

Every guardrail in the specification is enforced here rather than trusted to
the person writing the next template:

  * a sentence with no finding behind it cannot be produced, because a sentence
    is only ever produced from a finding;
  * causal wording is gated on the finding's own evidence, so a template that
    reaches for "נובע מ" without it simply never matches;
  * a placeholder with no value drops the whole rule instead of rendering a
    hole, and says so in the log;
  * section caps and the overall ceiling are applied to the assembled report,
    because an executive summary longer than fourteen sentences stops being one.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.findings.schema import Finding
from app.narrative.conditions import evaluate
from app.narrative.formatters import PLAIN, render_value

log = logging.getLogger("funneliq.narrative")

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

SECTION_ORDER = ("headline", "drivers", "funnel", "watch", "quality")
SECTION_CAPS = {"headline": 2, "drivers": 4, "funnel": 3, "watch": 3, "quality": 2}
# Deliberately below the sum of the caps. The sections are allowed to compete
# for the last few sentences rather than each spending its full allowance.
REPORT_CEILING = 14


@dataclass(frozen=True)
class Rule:
    rule_key: str
    applies_to: str
    priority: int
    conditions: list[dict]
    template_he: str
    section: str
    max_per_report: int = 1
    requires_fields: tuple[str, ...] = ()
    is_active: bool = True


@dataclass(frozen=True)
class Sentence:
    section: str
    ordinal: int
    rule_key: str
    finding_id: str
    text_he: str


def render(rule: Rule, finding: Finding, wrap=PLAIN) -> str | None:
    """
    Fill one template, or refuse.

    Refusing is the point. A template that asks for a field this finding does
    not carry has been applied to the wrong kind of finding, and printing the
    sentence with the gap closed up would produce something fluent and false.
    """
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        value = render_value(match.group(1), finding, wrap)
        if value is None:
            missing.append(match.group(1))
            return ""
        return value

    text = PLACEHOLDER.sub(replace, rule.template_he)
    if missing:
        log.info("rule %s skipped: no value for %s", rule.rule_key, ", ".join(missing))
        return None
    return re.sub(r"\s{2,}", " ", text).strip()


def match(rules: list[Rule], finding: Finding) -> Rule | None:
    """
    The highest-priority rule whose conditions all hold.

    First match wins after sorting, so priority is the only tie-breaker and two
    rules that could both describe a finding never both do. rule_key breaks a
    priority tie, which keeps the choice stable across runs — determinism has
    to survive a dictionary's iteration order.
    """
    candidates = [
        r for r in rules
        if r.is_active and r.applies_to == finding.finding_type
        and all(finding.field_value(f) is not None for f in r.requires_fields)
        and evaluate(r.conditions, finding)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-r.priority, r.rule_key))[0]


def compose(findings: list[Finding], rules: list[Rule], *, wrap=PLAIN) -> list[Sentence]:
    """
    Assemble the report.

    Findings are taken in severity order, so the caps bite on the least
    important sentences rather than on whichever happened to be computed last.
    """
    ordered = sorted(findings, key=lambda f: (-f.severity, str(f.finding_id)))

    used_rule_counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    by_section: dict[str, list[Sentence]] = {s: [] for s in SECTION_ORDER}

    for finding in ordered:
        rule = match(rules, finding)
        if rule is None:
            continue
        # One sentence per rule per slice of the data. The same rule firing
        # twice on the same segment is the same observation counted twice.
        signature = (rule.rule_key, repr(sorted(finding.dimension_path.items())))
        if signature in seen:
            continue
        if used_rule_counts.get(rule.rule_key, 0) >= rule.max_per_report:
            continue
        if len(by_section[rule.section]) >= SECTION_CAPS[rule.section]:
            continue

        text = render(rule, finding, wrap)
        if text is None:
            continue

        seen.add(signature)
        used_rule_counts[rule.rule_key] = used_rule_counts.get(rule.rule_key, 0) + 1
        by_section[rule.section].append(Sentence(
            section=rule.section, ordinal=len(by_section[rule.section]),
            rule_key=rule.rule_key, finding_id=str(finding.finding_id), text_he=text,
        ))

    out: list[Sentence] = []
    for section in SECTION_ORDER:
        for sentence in by_section[section]:
            if len(out) >= REPORT_CEILING:
                log.info("report ceiling of %d sentences reached", REPORT_CEILING)
                return out
            out.append(sentence)
    return out

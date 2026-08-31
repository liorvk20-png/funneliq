"""
Hebrew that reads as if a person wrote it.

A verb agrees with its subject in gender, so the same event is "עלתה" for
עלות and "עלה" for שיעור, and the pair itself changes with the kind of thing:
prices and rates rise and fall, quantities grow and shrink. Get either wrong
and the first line of the report announces that nobody read it — which is a
worse outcome for a document meant to carry authority than any arithmetic slip
buried on page two.

Everything here is a lookup. There is no inflection engine and there should not
be: the metric list is finite and hand-checked, and a general Hebrew morphology
that is right ninety percent of the time would be wrong in public.
"""
from __future__ import annotations

import re

from app.findings.schema import Direction, Gender, MetricDefinition, VerbFamily

VERBS: dict[tuple[VerbFamily, Direction, Gender], str] = {
    (VerbFamily.RISE_FALL, Direction.UP, Gender.M): "עלה",
    (VerbFamily.RISE_FALL, Direction.UP, Gender.F): "עלתה",
    (VerbFamily.RISE_FALL, Direction.DOWN, Gender.M): "ירד",
    (VerbFamily.RISE_FALL, Direction.DOWN, Gender.F): "ירדה",
    (VerbFamily.GROW_SHRINK, Direction.UP, Gender.M): "גדל",
    (VerbFamily.GROW_SHRINK, Direction.UP, Gender.F): "גדלה",
    (VerbFamily.GROW_SHRINK, Direction.DOWN, Gender.M): "קטן",
    (VerbFamily.GROW_SHRINK, Direction.DOWN, Gender.F): "קטנה",
}

# What a metric does when it does not move. Same agreement problem, no direction.
FLAT = {Gender.M: "נותר יציב", Gender.F: "נותרה יציבה"}


def verb(definition: MetricDefinition, direction: Direction) -> str:
    if direction == Direction.FLAT:
        return FLAT[definition.gender_he]
    return VERBS[(definition.verb_family, direction, definition.gender_he)]


def verb_up(definition: MetricDefinition) -> str:
    return verb(definition, Direction.UP)


def verb_down(definition: MetricDefinition) -> str:
    return verb(definition, Direction.DOWN)


def label_after_preposition(label: str) -> str:
    """
    A metric name as it appears after ב, ל, כ or מ.

    Hebrew merges the definite article into the preposition: ב + הרווח is
    ברווח, never בהרווח. Writing the template as "ב{m|label}" produced exactly
    that — "בהרווח", "בהתשואה" — which is the same class of error the gender
    field exists to prevent, introduced by a template that did not know its
    label already carried an article.

    Only a leading article is removed. "שיעור המענה" keeps its internal one,
    because the preposition attaches to the first word and that word has none.
    """
    return label[1:] if label.startswith("ה") else label


# Dimension values reach the reader as words, so they are translated rather than
# printed as the database spells them. A value with no translation is shown as
# it came: an untranslated label is a small blemish, and inventing a Hebrew name
# for a segment the company defined would be a lie about their own data.
DIMENSION_VALUES = {
    "budget_tier": {"Low": "תקציב נמוך", "Mid": "תקציב בינוני", "High": "תקציב גבוה"},
    "lead_volume_band": {"small": "נפח לידים נמוך", "medium": "נפח לידים בינוני",
                         "large": "נפח לידים גבוה"},
}

DIMENSION_LABELS = {
    "budget_tier": "רמת תקציב",
    "lead_volume_band": "נפח לידים",
}


# Anything shaped like it identifies a person. Dimension values arrive from a
# customer's own file, so the narrative cannot assume they are categories: a
# column the matcher placed as a dimension could hold an address, a phone number
# or a record id, and printing one into a report distributes it to everyone who
# reads that report.
IDENTIFIER_SHAPES = (
    re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"),      # an address
    re.compile(r"(?:\+?\d[\d\-\s()]{6,}\d)"),      # a phone number
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"),  # a uuid
    re.compile(r"^\d{7,}$"),                        # a bare record id
)
REDACTED = "ערך מזוהה"


def safe_value(dimension: str, value: str) -> str:
    """
    A dimension value fit to appear in a sentence.

    Known values are translated. Unknown ones pass through, because inventing a
    Hebrew name for a segment the company defined would misrepresent their own
    data. Anything shaped like an identifier is replaced instead of printed —
    the allow-list is what a value has to fail before it is treated as data
    rather than as a label.
    """
    known = DIMENSION_VALUES.get(dimension, {})
    if value in known:
        return known[value]
    text = str(value)
    if any(shape.search(text) for shape in IDENTIFIER_SHAPES):
        return REDACTED
    return text


def dimension_phrase(path: dict[str, str]) -> str:
    """
    A dimension path as a reader-facing phrase: {"budget_tier": "Mid"} becomes
    "תקציב בינוני". Several dimensions are joined with a separator rather than
    a conjunction, because "תקציב בינוני ונפח לידים גבוה" reads as two things
    while the finding is about their intersection.
    """
    if not path:
        return "כלל הקמפיינים"
    parts = [safe_value(k, v) for k, v in path.items()]
    return " · ".join(parts)

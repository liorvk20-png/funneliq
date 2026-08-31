"""
Turning field values into the words that appear in a sentence.

The RTL rule is the one that bites. A number inside a Hebrew sentence must
still read left to right, or the percent sign and the minus jump to the wrong
end of the digits — "12.4%-" instead of "-12.4%". It is not subtle and it is
the first thing a Hebrew reader sees. So every number goes through a wrapper,
and the HTML renderer wraps it in an isolating span while the plain-text one
leaves it alone.
"""
from __future__ import annotations

from collections.abc import Callable

from app.findings.metrics import metric
from app.findings.schema import Direction, Finding
from app.narrative.hebrew import dimension_phrase, label_after_preposition, verb

# How a number is isolated from the Hebrew around it. The default is plain
# text, for storage and for snapshot tests; the HTML renderer passes the other.
PLAIN: Callable[[str], str] = lambda text: text  # noqa: E731
HTML: Callable[[str], str] = lambda text: f'<span dir="ltr">{text}</span>'  # noqa: E731


def _thousands(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}"


def pct1(value: float) -> str:
    """A proportion as a percentage: 0.124 -> 12.4%."""
    return f"{value * 100:.1f}%"


def abs_pct1(value: float) -> str:
    """
    A magnitude, for templates that state the direction in words.

    "רכך אותו בשיעור של -74.6%" carries the sign twice and reads as a
    contradiction; the sentence already says which way the segment moved.
    """
    return f"{abs(value) * 100:.1f}%"


def pp1(value: float) -> str:
    """A difference between two percentages, in points rather than percent."""
    return f"{value * 100:.1f} נק' אחוז"


def money(value: float) -> str:
    return f"₪{_thousands(round(value))}"


def num0(value: float) -> str:
    return _thousands(round(value))


def num1(value: float) -> str:
    return _thousands(value, 1)


def ratio(value: float) -> str:
    return f"{value:.2f}x"


FORMATTERS: dict[str, Callable[[float], str]] = {
    "pct1": pct1, "abs_pct1": abs_pct1, "pp1": pp1, "money": money,
    "num0": num0, "num1": num1, "ratio": ratio,
}

# Which formatter a metric's own unit implies, for the `auto` spec. Without it
# every template would have to know whether the metric it is describing happens
# to be money, and templates would stop being reusable across metrics.
UNIT_FORMAT = {
    "currency": money, "percent": pct1, "count": num0,
    "days": num0, "months": num1, "ratio": ratio,
}


def render_value(spec: str, finding: Finding, wrap: Callable[[str], str]) -> str | None:
    """
    Resolve one `{field|format}` placeholder.

    Returns None when the field is absent, and the caller drops the whole rule
    rather than rendering a sentence with a gap in it. A report that silently
    prints "עלתה ב־" with nothing after it is worse than one sentence shorter.
    """
    field, _, fmt = spec.partition("|")
    field, fmt = field.strip(), fmt.strip()
    definition = metric(finding.metric_key)

    # Metric-level specs describe the metric itself, not a number on it.
    if field == "m":
        if fmt == "label":
            return definition.label_he
        if fmt == "label_after_b":
            return label_after_preposition(definition.label_he)
        if fmt == "verb_up":
            return verb(definition, Direction.UP)
        if fmt == "verb_down":
            return verb(definition, Direction.DOWN)
        if fmt == "verb_delta":
            return verb(definition, finding.direction or Direction.FLAT)
        return None
    if field == "d" and fmt == "dim":
        return dimension_phrase(finding.dimension_path)

    value = finding.field_value(field)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if fmt == "auto":
        return wrap(UNIT_FORMAT[definition.unit.value](value))
    formatter = FORMATTERS.get(fmt)
    if formatter is None:
        return None
    # A percentage delta is signed, and the sign carries the meaning. Absolute
    # value is taken only where the template supplies its own direction word.
    return wrap(formatter(abs(value) if fmt in ("pct1", "pp1") and field.endswith("_pct")
                          else value))

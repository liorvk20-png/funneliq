"""
The condition language rules are written in.

Deliberately tiny and deliberately closed. Rules live in the database, where a
customer-specific rule could one day be edited by someone who is not us, so the
evaluator has no eval, no lambda, no attribute traversal and no arbitrary
callable — a rule can only compare a named field against a literal with one of
nine operators. Anything a rule cannot express is a reason to add an operator
here, in code, under review.

Conditions in a list are joined with AND. There is no OR, and the absence is
not an oversight: two conditions that need OR are two rules with two different
priorities, which is also two separate sentences the author had to think about.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.findings.schema import Finding


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _cmp(op: Callable[[float, float], bool]):
    def run(actual: Any, expected: Any) -> bool:
        a, b = _num(actual), _num(expected)
        return False if a is None or b is None else op(a, b)
    return run


def _abs_gte(actual: Any, expected: Any) -> bool:
    a, b = _num(actual), _num(expected)
    return False if a is None or b is None else abs(a) >= b


def _between(actual: Any, expected: Any) -> bool:
    a = _num(actual)
    if a is None or not isinstance(expected, (list, tuple)) or len(expected) != 2:
        return False
    low, high = _num(expected[0]), _num(expected[1])
    return False if low is None or high is None else low <= a <= high


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b or (str(a) == str(b) if a is not None else False),
    "neq": lambda a, b: not (a == b or (str(a) == str(b) if a is not None else False)),
    "gt": _cmp(lambda a, b: a > b),
    "gte": _cmp(lambda a, b: a >= b),
    "lt": _cmp(lambda a, b: a < b),
    "lte": _cmp(lambda a, b: a <= b),
    "in": lambda a, b: a in b or str(a) in [str(x) for x in b],
    "between": _between,
    "abs_gte": _abs_gte,
    # `exists` asks whether the finding carries the field at all, which is the
    # difference between "we measured zero" and "we did not measure".
    "exists": lambda a, b: (a is not None) == bool(b),
}


def evaluate(conditions: list[dict], finding: Finding) -> bool:
    """
    True when every condition holds.

    An unknown operator is False rather than an exception: a malformed rule
    should stop matching, not stop the report. It is logged by the engine.
    """
    for condition in conditions:
        op = OPERATORS.get(condition.get("op", ""))
        if op is None:
            return False
        actual = finding.field_value(condition.get("field", ""))
        if actual is None and condition.get("op") != "exists":
            return False
        if not op(actual, condition.get("value")):
            return False
    return True

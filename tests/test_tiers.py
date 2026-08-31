"""
budget_tier is defined in two places and used in a third.

The database computes it as a generated column, and app/main.py's _budget
re-derives the same boundaries in Python to label the return curve. They must
agree, or a company sees a campaign counted as "Mid" in one panel and "High"
in the next.

There used to be a third copy, at inference: the served model was given a tier
it had to have learned. app/training.py drops budget_tier instead — it is a
fixed function of ad_budget, so a tree recovers the same splits from the number
itself, and a company's own tier boundaries may not be ours. That removed the
duplication rather than testing it, which is the better outcome and the reason
this file is shorter than it was.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "\n".join(p.read_text() for p in sorted((_ROOT / "migrations").glob("*.sql")))
MAIN = (_ROOT / "app" / "main.py").read_text()


def test_the_schema_still_defines_the_boundaries_the_dashboard_assumes():
    assert re.search(r"ad_budget\s*<=\s*1500\s*then\s*'Low'", SCHEMA, re.I)
    assert re.search(r"ad_budget\s*<=\s*5000\s*then\s*'Mid'", SCHEMA, re.I)


def test_the_budget_curve_uses_the_same_boundaries_as_the_database():
    """
    Two panels disagreeing about which tier a campaign is in is the kind of
    defect that reads as a rounding error and is not one.
    """
    assert '"Low" if budget <= 1500 else ("Mid" if budget <= 5000 else "High")' in MAIN


def test_training_does_not_reintroduce_a_third_definition():
    training = (_ROOT / "app" / "training.py").read_text()
    assert '"budget_tier"' in training  # named in the drop list
    assert "1500" not in training and "5000" not in training

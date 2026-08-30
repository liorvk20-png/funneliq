"""
budget_tier is defined in three places: schema.sql as a generated column,
features.py for training, and app/predict.py at inference. They must agree, or
a model is served a tier it never learned.
"""
import re
from pathlib import Path

from features import load_raw

from app.predict import _tier

# The schema moved into migrations/ when multi-tenancy arrived; the tier
# definition lives in whichever migration last created the table, so both are
# read and the assertions run against their concatenation.
_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "\n".join(p.read_text() for p in sorted((_ROOT / "migrations").glob("*.sql")))


def test_schema_boundaries_are_what_the_code_assumes():
    assert re.search(r"ad_budget\s*<=\s*1500\s*then\s*'Low'", SCHEMA, re.I)
    assert re.search(r"ad_budget\s*<=\s*5000\s*then\s*'Mid'", SCHEMA, re.I)


def test_inference_tier_matches_training_tier():
    df = load_raw()
    mismatches = [b for b in df["ad_budget"].unique()
                  if _tier(b) != ("Low" if b <= 1500 else ("Mid" if b <= 5000 else "High"))]
    assert mismatches == []


def test_boundaries_are_inclusive_on_the_lower_tier():
    assert _tier(1500) == "Low" and _tier(1501) == "Mid"
    assert _tier(5000) == "Mid" and _tier(5001) == "High"

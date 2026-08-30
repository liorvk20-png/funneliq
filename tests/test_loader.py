"""
Regression test for a real bug: df.where(pd.notnull(df), None) leaves NaN in
place on a float column, because pandas coerces None straight back. json.dumps
does not raise on NaN either — it emits a bare `NaN` token, invalid JSON that
PostgREST rejects. The load appeared correct right up until it failed.
"""
import json

import pandas as pd


def _records(df: pd.DataFrame, *, fixed: bool) -> list[dict]:
    frame = df.astype(object) if fixed else df
    return frame.where(pd.notnull(df), None).to_dict(orient="records")


def test_the_naive_version_really_does_leave_nan(csv):
    """Proves the bug is real rather than imagined, so the fix cannot be dropped."""
    bad = _records(csv, fixed=False)
    surviving = sum(1 for r in bad for v in r.values()
                    if isinstance(v, float) and v != v)
    assert surviving == 33


def test_astype_object_turns_missing_values_into_none(csv):
    good = _records(csv, fixed=True)
    assert sum(1 for r in good if r["ltv_months"] is None) == 4
    assert sum(1 for r in good if r["cumulative_profit"] is None) == 29
    assert not any(isinstance(v, float) and v != v for r in good for v in r.values())


def test_records_survive_strict_json(csv):
    """allow_nan=False is what the database effectively enforces."""
    json.dumps(_records(csv, fixed=True), allow_nan=False)

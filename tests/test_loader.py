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


# ------------------------------------------------------- tenancy of the load
# The loader holds the only unrestricted credential in the project: the service
# key, which bypasses RLS entirely. Every guarantee the database makes about
# isolation stops applying inside this file, so the scoping has to be checked
# here in the source rather than trusted to Postgres.


def _loader_source() -> str:
    """
    The loader's code, with its module docstring removed.

    The docstring quotes the dangerous old line verbatim to explain why it was
    removed, and searching the raw file would find that quotation and report a
    bug that is not there. Parsing the module and dropping the docstring keeps
    the explanation and the check from fighting each other.
    """
    import ast
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent
            / "scripts" / "load_csv_to_supabase.py").read_text()
    tree = ast.parse(text)
    body = tree.body[1:] if ast.get_docstring(tree) else tree.body
    return "\n".join(ast.unparse(node) for node in body)


def test_the_delete_is_scoped_to_one_company():
    """
    The original line was `.delete().neq("id", 0)` — every row in the table.
    Harmless with a single company and a total wipe of every customer's data
    the moment there are two, run by a script whose whole purpose is routine
    re-running. An unscoped delete here can never be right.
    """
    src = _loader_source()
    assert ".delete().eq('company_id', company_id)" in src
    assert ".neq('id', 0)" not in src


def test_rows_carry_the_company_that_owns_them():
    src = _loader_source()
    assert "r['company_id'] = company_id" in src


def test_a_failed_load_is_not_recorded_as_ready():
    """Partial data marked complete is worse than a load that visibly failed."""
    src = _loader_source()
    assert "'status': 'failed'" in src
    assert src.index("'status': 'failed'") < src.index("{'status': 'ready'}")

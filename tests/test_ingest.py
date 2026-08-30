"""
The upload gate.

Uploading is now the only way data enters the product, which makes this module
the only thing standing between a company's export and its dashboard. Two
failure modes matter and they pull in opposite directions: letting through a
file that will produce wrong numbers, and rejecting a file that is merely
untidy. Both are tested here.

Nothing in ingest touches the network or the database, so these run against
DataFrames directly rather than through an HTTP request.
"""
import pandas as pd
import pytest

from app.ingest import (
    BOOLEAN_COLUMNS,
    INTEGER_COLUMNS,
    MAX_ROWS,
    NULLABLE_COLUMNS,
    REQUIRED_COLUMNS,
    inspect,
    normalise_headers,
    to_records,
)


def good_frame(n=3) -> pd.DataFrame:
    row = {c: 5 for c in INTEGER_COLUMNS}
    row.update({"ad_budget": 2000, "num_leads": 40, "leads_answered": 25,
                "leads_not_answered": 15, "closed": 3, "not_closed": 12})
    row.update({c: "Yes" for c in BOOLEAN_COLUMNS})
    row.update({"ltv_months": 6.0, "cumulative_profit": 4200.0})
    return pd.DataFrame([row] * n)


# ------------------------------------------------------------ the real file
def test_the_reference_export_passes(csv):
    """The CSV the project was built on must still be an acceptable upload."""
    report, clean = inspect(csv)
    assert report.ok, report.errors
    assert report.rows == 3500 and len(clean) == 3500


# ------------------------------------------------------------- header names
@pytest.mark.parametrize("written", ["Ad Budget", "AD_BUDGET", "ad-budget", " ad_budget "])
def test_headers_are_matched_the_way_a_person_reads_them(written):
    df = good_frame().rename(columns={"ad_budget": written})
    assert "ad_budget" in normalise_headers(df).columns
    assert inspect(df)[0].ok


def test_a_missing_column_is_an_error_that_names_it():
    df = good_frame().drop(columns=["closed"])
    report, clean = inspect(df)
    assert not report.ok and clean is None
    assert report.missing_columns == ["closed"]
    assert "closed" in " ".join(report.errors)


def test_an_unknown_column_is_a_warning_not_a_rejection():
    """A company's export carrying its own extra columns is normal."""
    df = good_frame()
    df["campaign_name"] = "spring"
    report, _ = inspect(df)
    assert report.ok
    assert report.extra_columns == ["campaign_name"]
    assert any("campaign_name" in w for w in report.warnings)


# -------------------------------------------------------------- the values
@pytest.mark.parametrize("word,expected", [
    ("Yes", True), ("yes", True), ("TRUE", True), ("1", True), ("כן", True),
    ("No", False), ("false", False), ("0", False), ("לא", False),
])
def test_booleans_are_accepted_in_the_spellings_exports_actually_use(word, expected):
    df = good_frame()
    df["referred"] = word
    report, clean = inspect(df)
    assert report.ok, report.errors
    assert bool(clean["referred"].iloc[0]) is expected


def test_an_unreadable_boolean_is_refused_rather_than_guessed():
    """Guessing here would invent an outcome the company never reported."""
    df = good_frame()
    df["referred"] = df["referred"].astype(object)
    df.loc[0, "referred"] = "maybe"
    report, clean = inspect(df)
    assert not report.ok and clean is None


def test_text_where_a_number_belongs_is_an_error():
    df = good_frame()
    df["num_leads"] = df["num_leads"].astype(object)
    df.loc[1, "num_leads"] = "forty"
    report, _ = inspect(df)
    assert not report.ok
    assert "num_leads" in " ".join(report.errors)


def test_negative_counts_are_an_error():
    df = good_frame()
    df.loc[0, "closed"] = -1
    assert not inspect(df)[0].ok


@pytest.mark.parametrize("column", NULLABLE_COLUMNS)
def test_the_two_outcome_columns_may_be_empty(column):
    df = good_frame()
    df.loc[0, column] = None
    report, clean = inspect(df)
    assert report.ok
    assert report.missing_values[column] == 1
    assert clean[column].isna().sum() == 1


# ------------------------------------------------------------ file shape
def test_an_empty_file_is_refused():
    assert not inspect(good_frame(0))[0].ok


def test_a_file_over_the_row_cap_is_refused():
    df = pd.DataFrame({c: [0] * (MAX_ROWS + 1) for c in REQUIRED_COLUMNS})
    for c in BOOLEAN_COLUMNS:
        df[c] = "No"
    report, _ = inspect(df)
    assert not report.ok
    assert f"{MAX_ROWS:,}" in " ".join(report.errors)


# ------------------------------------------------------- arithmetic checks
def test_leads_that_do_not_add_up_are_flagged_but_still_stored():
    """
    A month where the parts do not sum to the whole gives wrong answer rates,
    and nothing downstream would ever reveal it. Refusing the whole file over a
    few rows helps nobody, so it warns and stores.
    """
    df = good_frame()
    df.loc[0, "leads_answered"] = 99
    report, clean = inspect(df)
    assert report.ok and len(clean) == 3
    assert any("ענו" in w for w in report.warnings)


def test_closing_deals_without_making_calls_is_flagged():
    df = good_frame()
    df.loc[0, "calls_to_closed"] = 0
    report, _ = inspect(df)
    assert report.ok
    assert any("אפס שיחות" in w for w in report.warnings)


def test_identical_rows_are_flagged_without_being_removed():
    """Might be a real repeat, might be a file exported twice. Not ours to decide."""
    report, clean = inspect(good_frame(3))
    assert len(clean) == 3
    assert any("זהות" in w for w in report.warnings)


def test_nothing_is_silently_repaired():
    """
    Every value stored must be a value the company sent. A number this module
    invented would flow into their averages and their models with nothing in
    the product to mark it as ours rather than theirs.
    """
    df = good_frame()
    df.loc[0, "leads_answered"] = 99
    df.loc[0, "cumulative_profit"] = None
    _, clean = inspect(df)
    assert clean["leads_answered"].iloc[0] == 99
    assert clean["cumulative_profit"].iloc[0] != clean["cumulative_profit"].iloc[0]  # NaN


# ------------------------------------------------------------- the records
def test_records_carry_their_owner_and_nothing_extra():
    df = good_frame()
    df["campaign_name"] = "spring"
    _, clean = inspect(df)
    rows = to_records(clean, "company-1", "upload-1")
    assert set(rows[0]) == set(REQUIRED_COLUMNS) | {"company_id", "upload_id"}
    assert rows[0]["company_id"] == "company-1"
    # The extra column was warned about; it must not reach the database, where
    # the insert would be rejected for a column that does not exist.
    assert "campaign_name" not in rows[0]


def test_missing_values_survive_as_none_and_not_as_nan():
    """
    json.dumps writes NaN as a bare `NaN` token — invalid JSON that PostgREST
    rejects. The original CSV load broke on exactly this.
    """
    import json
    df = good_frame()
    df.loc[0, "ltv_months"] = None
    _, clean = inspect(df)
    rows = to_records(clean, "c", "u")
    assert rows[0]["ltv_months"] is None
    assert "NaN" not in json.dumps(rows)


def test_counts_are_stored_as_integers():
    """A float in an integer column is rejected by Postgres, not coerced."""
    _, clean = inspect(good_frame())
    row = to_records(clean, "c", "u")[0]
    assert all(isinstance(row[c], int) for c in INTEGER_COLUMNS)


# --------------------------------------------------- documentation drift
def test_the_upload_page_documents_every_column_it_requires():
    """
    The page lists the columns for the person filling in the file. If the
    parser's list and the page's list drift apart, a company follows the
    documentation and gets a rejection it cannot explain.
    """
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent
            / "app" / "static" / "index.html").read_text()
    docs = page.split("const COLUMN_DOCS = [")[1].split("];")[0]
    # followup_1..5 are documented as one range rather than five identical rows.
    documented = {c for c in REQUIRED_COLUMNS if not c.startswith("followup_")}
    for column in documented:
        assert f'["{column}"' in docs, f"{column} is required but not documented"
    assert "followup_1 … followup_5" in docs

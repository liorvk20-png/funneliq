"""
Recognising a company's column names.

The whole point of this module is that a company does not have to rename its
columns to match ours. That convenience is only worth having if it is right,
because a mis-recognised column does not fail — it loads cleanly and reports
numbers that are quietly wrong.

The pairs below are the reason this file is long. "leads_answered" and
"leads_not_answered" differ by three characters, and every similarity measure
rates each as an excellent match for the other. Getting them the wrong way
round inverts every answer rate in the product with nothing anywhere to show
it. The same holds for closed/not_closed and the two call counts.
"""
import pytest

from app.ingest import REQUIRED_COLUMNS, inspect
from app.mapping import CONFIDENT, PLAUSIBLE, normalise, propose, score
from tests.test_ingest import good_frame

# source header, the column it means, the column it must not be mistaken for
OPPOSITES = [
    ("Answered", "leads_answered", "leads_not_answered"),
    ("Answered Leads", "leads_answered", "leads_not_answered"),
    ("Not Answered", "leads_not_answered", "leads_answered"),
    ("Unanswered", "leads_not_answered", "leads_answered"),
    ("No Answer", "leads_not_answered", "leads_answered"),
    ("ענו", "leads_answered", "leads_not_answered"),
    ("לא ענו", "leads_not_answered", "leads_answered"),
    ("ללא מענה", "leads_not_answered", "leads_answered"),
    ("Closed Won", "closed", "not_closed"),
    ("Closed Lost", "not_closed", "closed"),
    ("Lost deals", "not_closed", "closed"),
    ("Failed", "not_closed", "closed"),
    ("נסגרו", "closed", "not_closed"),
    ("לא נסגרו", "not_closed", "closed"),
    ("אבודים", "not_closed", "closed"),
    ("Calls to Close", "calls_to_closed", "calls_to_not_closed"),
    ("Wasted Calls", "calls_to_not_closed", "calls_to_closed"),
    ("שיחות עד סגירה", "calls_to_closed", "calls_to_not_closed"),
    ("שיחות ללא סגירה", "calls_to_not_closed", "calls_to_closed"),
    ("שיחות מבוזבזות", "calls_to_not_closed", "calls_to_closed"),
]


@pytest.mark.parametrize("header,right,wrong", OPPOSITES)
def test_a_column_is_never_mistaken_for_its_opposite(header, right, wrong):
    assert score(header, right) > score(header, wrong)


@pytest.mark.parametrize("header,right,wrong", OPPOSITES)
def test_the_opposite_is_not_merely_less_likely_but_refused(header, right, wrong):
    """
    A veto, not a penalty. If the wrong half were simply scored lower it could
    still win when the right half is absent from the file — which is exactly
    the case where the mistake is invisible.
    """
    assert score(header, wrong) < PLAUSIBLE


@pytest.mark.parametrize("header,right,_wrong", OPPOSITES)
def test_the_right_column_is_matched_confidently(header, right, _wrong):
    assert score(header, right) >= CONFIDENT


# ------------------------------------------------------- whole files
HEBREW = ["תקציב", "לידים", "ענו", "לא ענו", "מעקב 1", "מעקב 2", "מעקב 3", "מעקב 4",
          "מעקב 5", "לא נסגרו", "נסגרו", "שיחות עד סגירה", "שיחות ללא סגירה",
          "עלות גיוס לקוח", "שווי לקוח", "רכש", "מכירה נוספת", "רווח מצטבר", "הפנה"]

ENGLISH = ["Ad Spend", "Total Leads", "Answered Leads", "No Answer", "Follow up 1",
           "Follow-up 2", "FU3", "Stage 4", "followup5", "Closed Lost", "Closed Won",
           "Calls to Close", "Wasted Calls", "CAC", "Lifetime Value", "Purchased?",
           "Up-sell", "Net Profit", "Referral"]


@pytest.mark.parametrize("headers", [HEBREW, ENGLISH], ids=["hebrew", "english"])
def test_a_real_export_is_recognised_without_being_asked_anything(headers):
    result = propose(headers, REQUIRED_COLUMNS)
    assert result["unmatched"] == []
    assert result["uncertain"] == []
    assert len(result["matched"]) == len(REQUIRED_COLUMNS)


@pytest.mark.parametrize("headers", [HEBREW, ENGLISH], ids=["hebrew", "english"])
def test_each_header_is_used_once(headers):
    """Two columns claiming the same header would leave one silently absent."""
    used = [m["column"] for m in propose(headers, REQUIRED_COLUMNS)["matched"].values()]
    assert len(used) == len(set(used))


def test_columns_we_do_not_want_are_left_alone():
    result = propose([*HEBREW, "שם קמפיין", "אזור"], REQUIRED_COLUMNS)
    assert result["spare"] == ["שם קמפיין", "אזור"]


def test_a_file_of_meaningless_names_asks_rather_than_guesses():
    result = propose(["col_a", "col_b", "x1", "stuff"], REQUIRED_COLUMNS)
    assert len(result["unmatched"]) >= len(REQUIRED_COLUMNS) - 1


def test_a_middling_match_is_offered_as_a_suggestion_not_applied():
    """
    A person confirming a form tends to accept whatever it already says, so a
    guess we are unsure of has to arrive as an empty field with a suggestion
    beside it rather than as a filled-in answer.
    """
    df = good_frame().rename(columns={"cumulative_profit": "profit margin pct"})
    report, clean = inspect(df)
    assert clean is None
    question = next(q for q in report.questions if q["column"] == "cumulative_profit")
    assert question["suggestion"] is None or question["confidence"] < CONFIDENT


# ------------------------------------------------------------- normalising
@pytest.mark.parametrize("written", [
    "Ad Budget", "ad-budget", "AD_BUDGET", " ad budget ", "Ad.Budget", "ad/budget",
])
def test_punctuation_and_case_do_not_change_a_name(written):
    assert normalise(written) == "ad budget"


# ------------------------------------------------- answering the questions
def test_an_answer_from_the_person_is_used():
    df = good_frame().rename(columns={"closed": "מה שיצא בסוף"})
    assert inspect(df)[0].questions, "should have asked"
    report, clean = inspect(df, {"closed": "מה שיצא בסוף"})
    assert report.ok, report.errors
    assert clean is not None and "closed" in clean.columns


def test_an_answer_naming_a_column_that_is_not_in_the_file_is_ignored():
    """
    The mapping arrives from the browser and is checked against the file rather
    than trusted. A rename to a header that does not exist would produce a
    column of nothing that looks like real data.
    """
    df = good_frame().rename(columns={"closed": "מה שיצא בסוף"})
    report, clean = inspect(df, {"closed": "עמודה שלא קיימת"})
    assert not report.ok and clean is None


def test_an_answer_overrides_a_confident_guess():
    """The person has seen the file; the matcher has seen the header."""
    df = good_frame()
    df["deals"] = df["closed"] * 2
    report, clean = inspect(df, {"closed": "deals"})
    assert report.ok
    assert clean["closed"].iloc[0] == df["deals"].iloc[0]


def test_the_questions_carry_examples_from_the_file():
    """
    Deciding what a column is from its name alone is the thing the matcher just
    failed at, so the person is shown what is actually in it.
    """
    df = good_frame().rename(columns={"closed": "zzz"})
    report, _ = inspect(df)
    assert "zzz" in report.samples
    assert len(report.samples["zzz"]) == 3
    assert next(q for q in report.questions if q["column"] == "closed")["options"] == ["zzz"]

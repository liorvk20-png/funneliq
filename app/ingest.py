"""
Turning a company's CSV into rows we are willing to store.

This is the only door data enters the product through now, so it is also the
only place that can stop bad data getting in. It is kept out of main.py and
free of any database or HTTP dependency, so the rules can be tested directly
against a DataFrame instead of through an upload.

Two kinds of finding, deliberately separated:

  errors    the file cannot be stored at all — a missing column, a value that
            is not a number, more rows than we accept.
  warnings  the file will store fine but something in it does not add up, such
            as answered plus unanswered leads not matching the total. These are
            shown and the upload proceeds, because a real export often has a
            few odd rows and refusing the whole month over three of them helps
            nobody.

Silently repairing anything is not an option here. A number the company never
sent would flow into their averages and into their models, and they would have
no way of knowing which figures were theirs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Every column the funnel_records table needs. Order matters only for messages.
INTEGER_COLUMNS = [
    "ad_budget", "num_leads", "leads_answered", "leads_not_answered",
    "followup_1", "followup_2", "followup_3", "followup_4", "followup_5",
    "not_closed", "closed", "calls_to_closed", "calls_to_not_closed",
    "customer_acquisition_cost",
]
BOOLEAN_COLUMNS = ["purchased", "upsell", "referred"]
# The only two columns allowed to be empty. Both are outcomes that may not have
# been measured yet for a recent campaign, and the aggregates already skip them.
NULLABLE_COLUMNS = ["ltv_months", "cumulative_profit"]

REQUIRED_COLUMNS = INTEGER_COLUMNS + BOOLEAN_COLUMNS + NULLABLE_COLUMNS

# Generous enough for years of monthly exports, small enough that a mistaken
# file cannot fill the database before anyone notices.
MAX_ROWS = 50_000
MAX_BYTES = 10 * 1024 * 1024

# A boolean arrives spelled a dozen ways depending on who exported the file.
# Accepting the common ones costs nothing; guessing at anything else would mean
# inventing an answer, so anything unrecognised is an error the company sees.
TRUE_WORDS = {"true", "yes", "y", "1", "1.0", "כן"}
FALSE_WORDS = {"false", "no", "n", "0", "0.0", "לא"}


@dataclass
class Report:
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_values: dict[str, int] = field(default_factory=dict)
    preview: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "rows": self.rows, "columns": self.columns,
            "missingColumns": self.missing_columns, "extraColumns": self.extra_columns,
            "errors": self.errors, "warnings": self.warnings,
            "missingValues": self.missing_values, "preview": self.preview,
        }


def normalise_headers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Match headers the way a person would: ignoring case, spaces and hyphens.

    "Ad Budget", "ad-budget" and "AD_BUDGET" are the same column to anyone
    reading the file, and rejecting a month's data over a capital letter would
    be the product being pedantic rather than careful.
    """
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("-", "_")
                  for c in df.columns]
    return df


def _to_bool(series: pd.Series) -> tuple[pd.Series, int]:
    """Returns the parsed column and how many values could not be read."""
    text = series.astype(str).str.strip().str.lower()
    out = text.map(lambda v: True if v in TRUE_WORDS else (False if v in FALSE_WORDS else None))
    return out, int(out.isna().sum())


def inspect(df: pd.DataFrame) -> tuple[Report, pd.DataFrame | None]:
    """
    Check a parsed CSV and, when it is storable, hand back the cleaned frame.

    The frame is returned rather than re-derived later so that what was checked
    and what gets stored are the same object — a second parse could differ from
    the first and nothing would notice.
    """
    r = Report()
    df = normalise_headers(df)
    r.columns = list(df.columns)
    r.rows = len(df)

    r.missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    r.extra_columns = [c for c in df.columns if c not in REQUIRED_COLUMNS]

    if r.missing_columns:
        r.errors.append(
            "חסרות עמודות בקובץ: " + ", ".join(r.missing_columns) +
            ". שנה את שמות העמודות בקובץ כך שיתאימו, ונסה שוב."
        )
    if r.extra_columns:
        r.warnings.append(
            "עמודות שלא נשמרות ויתעלמו מהן: " + ", ".join(r.extra_columns) + "."
        )
    if r.rows == 0:
        r.errors.append("הקובץ ריק — אין בו אף שורת נתונים.")
    if r.rows > MAX_ROWS:
        r.errors.append(f"בקובץ {r.rows:,} שורות. המקסימום הוא {MAX_ROWS:,}.")
    if r.errors:
        return r, None

    clean = pd.DataFrame(index=df.index)

    for col in INTEGER_COLUMNS:
        values = pd.to_numeric(df[col], errors="coerce")
        unreadable = int(values.isna().sum())
        if unreadable:
            r.errors.append(
                f"בעמודה {col} יש {unreadable:,} ערכים שאינם מספר. "
                "כל השורות חייבות ערך מספרי בעמודה הזו."
            )
        negative = int((values < 0).sum())
        if negative:
            r.errors.append(f"בעמודה {col} יש {negative:,} ערכים שליליים.")
        clean[col] = values

    for col in BOOLEAN_COLUMNS:
        parsed, unreadable = _to_bool(df[col])
        if unreadable:
            r.errors.append(
                f"בעמודה {col} יש {unreadable:,} ערכים שלא ניתן לקרוא כ״כן״ או ״לא״. "
                "ערכים מקובלים: Yes/No, True/False, 1/0, כן/לא."
            )
        clean[col] = parsed

    for col in NULLABLE_COLUMNS:
        values = pd.to_numeric(df[col], errors="coerce")
        blank = int(values.isna().sum())
        r.missing_values[col] = blank
        if blank:
            r.warnings.append(
                f"בעמודה {col} חסרים {blank:,} ערכים מתוך {r.rows:,}. "
                "השורות האלה יישמרו, והממוצעים יחושבו בלעדיהן."
            )
        clean[col] = values

    if r.errors:
        return r, None

    _consistency_warnings(clean, r)

    duplicates = int(clean.duplicated().sum())
    if duplicates:
        r.warnings.append(
            f"{duplicates:,} שורות זהות לחלוטין לשורה אחרת. הן יישמרו כמו שהן — "
            "ייתכן שהן אמיתיות, וייתכן שהקובץ יוצא פעמיים."
        )

    r.preview = (
        clean.head(5).astype(object).where(pd.notnull(clean.head(5)), None)
        .to_dict(orient="records")
    )
    return r, clean


def _consistency_warnings(clean: pd.DataFrame, r: Report) -> None:
    """
    Arithmetic the file should satisfy on its own terms.

    These catch the export that is wrong rather than merely unreadable: a month
    where the parts do not add up to the whole is a month whose conversion rates
    will be wrong, and nothing later in the product would reveal it.
    """
    mismatch = int((clean["leads_answered"] + clean["leads_not_answered"]
                    != clean["num_leads"]).sum())
    if mismatch:
        r.warnings.append(
            f"ב־{mismatch:,} שורות, ״ענו״ ועוד ״לא ענו״ לא מסתכמים למספר הלידים. "
            "שיעורי המענה בשורות האלה יהיו שגויים."
        )

    over = int((clean["closed"] + clean["not_closed"] > clean["leads_answered"]).sum())
    if over:
        r.warnings.append(
            f"ב־{over:,} שורות מספר הסגירות והאי־סגירות גדול ממספר מי שענו לטלפון."
        )

    silent = int(((clean["closed"] > 0) & (clean["calls_to_closed"] == 0)).sum())
    if silent:
        r.warnings.append(
            f"ב־{silent:,} שורות יש סגירות אבל אפס שיחות עד סגירה."
        )

    free = int((clean["ad_budget"] == 0).sum())
    if free:
        r.warnings.append(
            f"ב־{free:,} שורות התקציב הוא אפס. חישובי תשואה לשקל ידלגו עליהן."
        )


def to_records(clean: pd.DataFrame, company_id: str, upload_id: str) -> list[dict]:
    """
    The cleaned frame as rows the database will accept.

    .astype(object) before .where() is not cosmetic: on a float column pandas
    turns None straight back into NaN, and json.dumps emits NaN as a bare token
    that is not valid JSON. Casting first lets the column actually hold None,
    which becomes SQL NULL. The same bug once broke the original CSV load.
    """
    frame = clean[INTEGER_COLUMNS + BOOLEAN_COLUMNS + NULLABLE_COLUMNS]
    records = frame.astype(object).where(pd.notnull(frame), None).to_dict(orient="records")
    for row in records:
        for col in INTEGER_COLUMNS:
            row[col] = int(row[col])
        row["company_id"] = company_id
        row["upload_id"] = upload_id
    return records

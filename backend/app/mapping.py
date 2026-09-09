"""
Working out which column in a company's file is which.

A company should not have to rename its columns to match ours. Their export
says "תקציב" or "Ad Spend" or "budget_ils", and all three mean ad_budget. This
module reads a set of headers and proposes an assignment, saying for each one
how sure it is, so the interface can confirm the doubtful ones with the person
instead of guessing on their behalf.

The dangerous failure here is not a column we fail to recognise — that one is
visible, and the person is asked. It is a column we recognise **wrongly**:
"leads_not_answered" and "leads_answered" differ by three characters, and any
plain similarity score ranks each as an excellent match for the other. Swapping
them produces a file that loads cleanly, reports answer rates that are exactly
inverted, and gives no sign anywhere that anything is wrong. So negation is
handled before similarity is ever consulted, and it can only veto a match, not
create one.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Above this, the match is taken without asking. Set where an exact hit on a
# known alias lands and a mere family resemblance does not.
CONFIDENT = 0.88
# Below this, no suggestion is offered at all; a wrong default is worse than an
# empty one, because a person confirming a form tends to accept what it says.
PLAUSIBLE = 0.60

# Words that flip a column's meaning. A header carrying one of these can never
# match a candidate that does not, and the reverse.
#
# "lost", "failed" and "wasted" are here even though they contain no negation,
# because they mean the negative half of a pair just as surely as "not closed"
# does. Leaving them out was not a gap but a live bug: "Lost deals" was vetoed
# against not_closed, whose canonical name carries a "not", and then matched
# `closed` at 0.82 through the shared word "deals" — a header meaning the exact
# opposite of the column it was about to be loaded into.
NEGATIONS = {
    "not", "no", "un", "without", "lost", "failed", "fail", "wasted", "unsuccessful",
    "לא", "ללא", "אי", "אבוד", "אבודים", "מבוזבז", "מבוזבזות", "נכשל", "נכשלו",
}

# Spellings seen in real exports, in both languages. The canonical name itself
# is always matched too and is not repeated here.
ALIASES: dict[str, list[str]] = {
    "ad_budget": ["budget", "ad spend", "spend", "campaign budget", "cost",
                  "media spend", "תקציב", "תקציב פרסום", "תקציב קמפיין", "הוצאה",
                  "עלות קמפיין", "השקעה"],
    "num_leads": ["leads", "total leads", "lead count", "number of leads", "enquiries",
                  "לידים", "מספר לידים", "סך לידים", "כמות לידים", "פניות"],
    "leads_answered": ["answered", "answered leads", "contacted", "reached", "picked up",
                       "ענו", "נענו", "לידים שענו", "לידים שנענו", "השיבו"],
    "leads_not_answered": ["not answered", "unanswered", "no answer", "unreachable",
                           "לא ענו", "לא נענו", "ללא מענה", "לא השיבו"],
    "followup_1": ["follow up 1", "followup1", "fu1", "stage 1", "מעקב 1", "שיחה 1",
                   "פולואפ 1"],
    "followup_2": ["follow up 2", "followup2", "fu2", "stage 2", "מעקב 2", "שיחה 2",
                   "פולואפ 2"],
    "followup_3": ["follow up 3", "followup3", "fu3", "stage 3", "מעקב 3", "שיחה 3",
                   "פולואפ 3"],
    "followup_4": ["follow up 4", "followup4", "fu4", "stage 4", "מעקב 4", "שיחה 4",
                   "פולואפ 4"],
    "followup_5": ["follow up 5", "followup5", "fu5", "stage 5", "מעקב 5", "שיחה 5",
                   "פולואפ 5"],
    "closed": ["closed deals", "won", "deals", "sales", "conversions", "closed won",
               "נסגרו", "סגירות", "עסקאות שנסגרו", "מכירות", "עסקאות"],
    "not_closed": ["not closed", "lost", "lost deals", "closed lost", "failed",
                   "לא נסגרו", "אבודים", "עסקאות שלא נסגרו"],
    "calls_to_closed": ["calls to close", "calls to closed", "calls per close",
                        "average calls", "touches to close",
                        "שיחות עד סגירה", "שיחות לסגירה", "ממוצע שיחות לסגירה"],
    "calls_to_not_closed": ["calls to not closed", "wasted calls", "calls without close",
                            "calls to lost", "שיחות ללא סגירה", "שיחות מבוזבזות",
                            "שיחות שלא הובילו לסגירה"],
    "customer_acquisition_cost": ["cac", "acquisition cost", "cost per customer",
                                  "cost per acquisition", "cpa",
                                  "עלות גיוס", "עלות גיוס לקוח", "עלות רכישת לקוח",
                                  "עלות ללקוח"],
    "ltv_months": ["ltv", "lifetime value", "ltv months", "customer lifetime",
                   "retention months", "שווי לקוח", "שווי לקוח בחודשים",
                   "אורך חיי לקוח", "חודשי לקוח"],
    "cumulative_profit": ["profit", "total profit", "revenue", "net profit", "margin",
                          "רווח", "רווח מצטבר", "רווח כולל", "הכנסה"],
    "purchased": ["purchase", "bought", "did purchase", "converted",
                  "רכש", "רכישה", "קנה", "בוצעה רכישה"],
    "upsell": ["up sell", "upsold", "cross sell", "expansion",
               "מכירה נוספת", "שדרוג", "מכירת המשך"],
    "referred": ["referral", "referrals", "recommended", "refer",
                 "הפניה", "הפניות", "המליץ", "הפנה", "המלצה"],
}


def normalise(text: str) -> str:
    """
    A header reduced to the words in it.

    Case, punctuation, separators and Hebrew vowel marks all vary between
    exports and none of them carry meaning, so they go. What is left is
    lowercase words separated by single spaces.
    """
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[_\-/\\.()\[\]{}:;,'\"#]+", " ", text.lower())
    text = re.sub(r"[^0-9a-z֐-׿ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Hebrew glues its conjunctions and prepositions onto the front of the next
# word, so the negation in "שיחות שלא הובילו לסגירה" is spelled "שלא" and a
# plain lookup for "לא" misses it. That miss was not theoretical: it let
# "שיחות עד סגירה" score 0.65 against calls_to_not_closed, over the threshold
# at which a match is offered at all.
HEBREW_PREFIXES = "ושבכלמה"


def _negated(text: str) -> bool:
    words = set(normalise(text).split())
    if words & NEGATIONS:
        return True
    if any(w[0] in HEBREW_PREFIXES and w[1:] in NEGATIONS for w in words if len(w) > 1):
        return True
    # "unanswered" carries the negation inside a single word.
    return any(w.startswith("un") and len(w) > 4 for w in words)


def _similarity(source: str, candidate: str) -> float:
    a, b = normalise(source), normalise(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Whole words shared matter more than characters shared: "total leads" and
    # "leads" are the same column, while "closed" and "closer" are not.
    wa, wb = set(a.split()), set(b.split())
    overlap = len(wa & wb) / max(len(wa | wb), 1)
    return max(ratio, (ratio + overlap * 2) / 3)


def score(source: str, canonical: str) -> float:
    """
    How well one header matches one of our columns, between 0 and 1.

    The negation check is a veto and never a boost: a header and a candidate
    that disagree about whether they mean "answered" or "not answered" are not
    a partial match to weigh against a high similarity score, they are the
    wrong column. Nothing else in this file can distinguish them — they differ
    by three characters and every similarity measure rates each as an excellent
    match for the other.

    It is applied against each candidate spelling rather than against the
    canonical name alone. not_closed's canonical name carries a "not" while its
    alias "lost" does not, and vetoing on the canonical name would rule out the
    very spelling that was added to catch this case.
    """
    negated = _negated(source)
    return max(
        (_similarity(source, c)
         for c in [canonical, *ALIASES.get(canonical, [])]
         if _negated(c) == negated),
        default=0.0,
    )


def propose(headers: list[str], canonical: list[str]) -> dict:
    """
    Assign the file's headers to our columns, best matches first.

    Greedy over every (header, column) pair rather than column by column: the
    strongest match in the whole file is settled first, so a header that is a
    good fit for two columns goes to the one it fits better, instead of to
    whichever column happened to be considered first.

    Returns, for each of our columns, either a match or nothing, along with a
    confidence and the headers still unclaimed. Deciding what to do with a
    middling confidence is the interface's job, not this function's.
    """
    pairs = sorted(
        ((score(h, c), h, c) for h in headers for c in canonical),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    matched: dict[str, dict] = {}
    used: set[str] = set()
    for value, header, column in pairs:
        if value < PLAUSIBLE or column in matched or header in used:
            continue
        matched[column] = {
            "column": header,
            "confidence": round(value, 3),
            "certain": value >= CONFIDENT,
        }
        used.add(header)

    return {
        "matched": matched,
        "uncertain": sorted(c for c, m in matched.items() if not m["certain"]),
        "unmatched": [c for c in canonical if c not in matched],
        "spare": [h for h in headers if h not in used],
    }

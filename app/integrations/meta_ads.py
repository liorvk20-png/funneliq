"""
Pulling campaign rows from a company's own Meta Ads account.

Kept separate from app/ingest.py on purpose: ingest.py is the one door data
enters the product through, and this module's only job is to produce a
DataFrame shaped exactly like a CSV would be, then hand it to that same
door. Nothing downstream needs to know a row came from Meta instead of a
file -- app.ingest.inspect() and app.ingest.to_records() are unchanged.

The honest limit of this integration, stated once here rather than left
implicit: Meta Ads can only ever describe what happened on Meta's side of
the funnel -- who saw an ad, who clicked, who filled a lead form, and what
it cost. It has no visibility into what a company's own sales team did
with those leads afterwards. Every column in funnel_records past
`ad_budget`, `num_leads` and `customer_acquisition_cost` (follow-up calls,
closes, purchases, upsells, referrals, LTV, profit) describes that
downstream work, and no ad platform can supply it. Rows built here fill
those columns with the honest "not yet known" default -- zero calls, zero
closes, nothing purchased -- not a guess, so that a company reviewing the
draft sees plainly which numbers are real and which ones are still theirs
to enter, the same way an empty cell in a CSV would read.
"""
from __future__ import annotations

import pandas as pd

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Meta's action_type for a completed lead form. "onsite_conversion.lead_grouped"
# covers Instant Forms; "lead" covers off-platform lead events some accounts
# report under. Both are counted so an account using either style is read
# correctly rather than silently under-counted.
LEAD_ACTION_TYPES = {"lead", "onsite_conversion.lead_grouped"}


class MetaAdsError(Exception):
    """Raised for anything the person can act on: a bad token, a bad account
    id, or Meta's API being unreachable. The message is shown as-is, so it is
    written in Hebrew and says what to check rather than what broke."""


def _leads_from_actions(actions: list[dict] | None) -> int:
    if not actions:
        return 0
    total = 0
    for action in actions:
        if action.get("action_type") in LEAD_ACTION_TYPES:
            try:
                total += int(float(action.get("value", 0)))
            except (TypeError, ValueError):
                continue
    return total


def fetch_campaign_insights(
    access_token: str, ad_account_id: str, since: str, until: str,
) -> list[dict]:
    """
    One dict per campaign that spent money in [since, until] (YYYY-MM-DD).

    A network or auth failure becomes a MetaAdsError with a message the
    person can act on, instead of an httpx exception with a stack trace in
    it -- the same shape app.main.py already expects from app.ingest.
    """
    import httpx  # imported lazily: only the Meta integration needs it

    account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
    url = f"{GRAPH_API_BASE}/{account}/insights"
    params = {
        "access_token": access_token,
        "level": "campaign",
        "fields": "campaign_id,campaign_name,spend,actions",
        "time_range": '{"since":"%s","until":"%s"}' % (since, until),
        "limit": 500,
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise MetaAdsError(
            "לא הצלחנו להתחבר ל-Meta. אפשר לנסות שוב בעוד רגע."
        ) from exc

    if resp.status_code == 401 or resp.status_code == 400:
        # Meta returns 400 for an expired/invalid token too, so both are
        # treated as "the token is no longer good" rather than guessed apart.
        raise MetaAdsError(
            "הטוקן של Meta לא תקף או פג תוקף. חבר מחדש את חשבון המודעות."
        )
    if resp.status_code == 403:
        raise MetaAdsError(
            "לטוקן הזה אין הרשאה לקרוא את חשבון המודעות. ודא שסימנת "
            "ads_read בעת יצירת הטוקן, ושהחשבון שלך הוא admin או "
            "analyst על חשבון המודעות."
        )
    if resp.status_code != 200:
        raise MetaAdsError(
            f"Meta החזירה שגיאה ({resp.status_code}). אפשר לנסות שוב, "
            "ואם זה חוזר — לבדוק את מזהה חשבון המודעות."
        )

    payload = resp.json()
    rows = payload.get("data", [])
    if not rows:
        raise MetaAdsError(
            f"לא נמצאו קמפיינים עם הוצאה בין {since} ל-{until} בחשבון הזה."
        )
    return rows


def map_to_funnel_frame(insights: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    """
    Meta's raw per-campaign insights, as a DataFrame shaped like ingest.py
    expects -- plus the display-only campaign names, in the same row order,
    for the review screen. Names are not stored: funnel_records has no
    campaign-name column (see migrations/001_initial.sql), the same way a
    CSV upload's own column would be dropped as "extra" if it had one.
    """
    rows = []
    names = []
    for entry in insights:
        spend = float(entry.get("spend", 0) or 0)
        leads = _leads_from_actions(entry.get("actions"))
        budget = round(spend)
        cac = round(spend / leads) if leads else 0
        rows.append({
            "ad_budget": budget,
            "num_leads": leads,
            # Meta cannot know who was called yet -- these start as "not yet
            # answered" rather than a guess, and still sum correctly to
            # num_leads so the file's own consistency check does not flag a
            # row nobody has touched yet.
            "leads_answered": 0,
            "leads_not_answered": leads,
            "followup_1": 0, "followup_2": 0, "followup_3": 0,
            "followup_4": 0, "followup_5": 0,
            "not_closed": 0, "closed": 0,
            "calls_to_closed": 0, "calls_to_not_closed": 0,
            "customer_acquisition_cost": cac,
            "purchased": False, "upsell": False, "referred": False,
            "ltv_months": None, "cumulative_profit": None,
        })
        names.append(entry.get("campaign_name") or entry.get("campaign_id") or "קמפיין")

    from app.ingest import BOOLEAN_COLUMNS, INTEGER_COLUMNS, NULLABLE_COLUMNS
    columns = INTEGER_COLUMNS + BOOLEAN_COLUMNS + NULLABLE_COLUMNS
    frame = pd.DataFrame(rows, columns=columns)
    return frame, names

from dotenv import load_dotenv

# Populate the environment from .env before app.auth / app.db read it at import
# time. On Railway there is no .env file and this is a harmless no-op — the
# platform supplies the same variables directly.
load_dotenv()

import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import uuid  # noqa: E402
from datetime import date  # noqa: E402
from functools import lru_cache  # noqa: E402

import pandas as pd  # noqa: E402
from fastapi import (  # noqa: E402
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.accounts import Ambiguous, email_for_company, looks_like_email, translate  # noqa: E402
from app.auth import get_current_user_token  # noqa: E402
from app.config import require_env  # noqa: E402
from app.db import get_anon_client, get_service_client, get_user_client  # noqa: E402
from app.ingest import MAX_BYTES, inspect, to_records  # noqa: E402
from app.training import MIN_ROWS, load, predict_one, train  # noqa: E402

app = FastAPI(title="FunnelIQ")

log = logging.getLogger("funneliq")


@app.exception_handler(Exception)
def unhandled(request: Request, exc: Exception):
    """
    Anything nobody anticipated.

    Without this, an unexpected failure reaches the browser as Starlette's bare
    "Internal Server Error" and reaches us as nothing at all — which is exactly
    what happened on a first sign-in that worked fine after a reload, leaving no
    way to tell what had broken. The reference goes to the person and the
    traceback goes to the log under the same value, so a report of "I saw an
    error" becomes a line we can actually find.

    The exception text is deliberately not sent to the browser: it can carry
    connection strings and internal paths.
    """
    reference = uuid.uuid4().hex[:8]
    log.exception("unhandled error [%s] on %s %s", reference, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"שגיאה לא צפויה בשרת. מספר לאיתור: {reference}"},
    )


@app.get("/health")
def health():
    """Public, no auth. Confirms the service is up — this is the one Railway checks."""
    return {"status": "ok"}


@app.get("/api/config")
def config():
    """
    Public on purpose. The browser needs the project URL and the publishable
    key to talk to Supabase Auth, and the publishable key is designed to be
    seen by clients — it grants nothing on its own, because RLS decides what a
    caller may read. The secret key is never sent here.
    """
    return {
        "supabaseUrl": require_env("SUPABASE_URL"),
        "publishableKey": require_env("SUPABASE_PUBLISHABLE_KEY"),
    }


# =====================================================================
# ACCOUNTS
# =====================================================================
# Sign-in and sign-up run through the server rather than straight from the
# browser to Supabase. Two reasons, and only one of them is cosmetic: every
# refusal reaches the person in Hebrew from a single place, and signing in by
# company name needs a lookup the browser is not allowed to make.


def _session(auth_response) -> dict:
    """The parts of a Supabase session the browser actually stores."""
    session = auth_response.session
    if session is None:
        # Sign-up with email confirmation switched on: the account exists and
        # there is no session until the link is clicked.
        return {"pending": True}
    return {
        "access_token": session.access_token,
        "expires_in": session.expires_in,
        "user": {"email": auth_response.user.email if auth_response.user else None},
    }


@app.post("/api/login")
def login(payload: dict):
    identifier = str(payload.get("identifier", "")).strip()
    password = str(payload.get("password", ""))
    if not identifier or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "יש למלא את שני השדות.")

    email = identifier
    if not looks_like_email(identifier):
        try:
            # The one place a request handler reaches for the secret key. It is
            # not reading anyone's data: there is no signed-in user yet, so
            # there is no RLS context to respect, and the address it finds is
            # used to authenticate and never returned. Doing this lookup in the
            # browser, or through a public endpoint, would turn a guessed
            # company name into someone's email address.
            email = email_for_company(get_service_client(), identifier)
        except Ambiguous:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "יש יותר מחשבון אחד עם שם החברה הזה. התחבר עם כתובת האימייל שלך.",
            ) from None
        if email is None:
            # Deliberately the same refusal a wrong password gets, so that
            # guessing company names reveals nothing about which ones exist.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                translate("Invalid login credentials"),
            )

    try:
        result = get_anon_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            translate(str(getattr(exc, "message", exc)))) from None
    return _session(result)


@app.post("/api/signup")
def signup(payload: dict):
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    company = str(payload.get("company", "")).strip()
    if not email or not password or not company:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "יש למלא את כל השדות.")
    if len(company) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "שם החברה ארוך מדי (עד 120 תווים).")
    try:
        # company_name travels in user metadata because the database trigger
        # reads it from there to build the workspace. Nothing here creates a
        # company; a request that skipped this field would produce an account
        # named after its email domain.
        result = get_anon_client().auth.sign_up({
            "email": email, "password": password,
            "options": {"data": {"company_name": company}},
        })
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            translate(str(getattr(exc, "message", exc)))) from None
    return _session(result)


@app.get("/api/funnel-records/sample")
def funnel_records_sample(token: str = Depends(get_current_user_token)):
    """
    First real 'reads from Supabase at runtime' endpoint, gated by the
    signed-in user's JWT. Row Level Security on funnel_records decides who
    gets rows — this function doesn't; it just forwards the user's token.
    """
    client = get_user_client(token)
    result = client.table("funnel_records").select("*").limit(10).execute()
    return {"count": len(result.data), "records": result.data}


# Roughly thirty times the current table. High enough never to trip on real
# growth, low enough that a broken pager fails in seconds.
MAX_ROWS = 100_000


def _fetch_all(client, columns: str, page: int = 1000) -> list[dict]:
    """
    Read every row, a page at a time.

    PostgREST caps a response at 1,000 rows by default and says nothing about
    it — no error, no flag, just a short list. An aggregate built on that comes
    out looking entirely plausible while describing under a third of the data,
    which is worse than an outright failure because nothing prompts you to
    check. Paging until a short page arrives is what makes the totals real.
    """
    out: list[dict] = []
    start = 0
    # A page that does not advance would spin here forever. Found by mutating
    # the range() call away and watching the test suite hang rather than fail:
    # a server that stops honouring the range parameter should surface as an
    # error, not as a request that never returns.
    while start <= MAX_ROWS:
        batch = (
            client.table("funnel_records")
            .select(columns)
            .range(start, start + page - 1)
            .execute()
            .data
        )
        out.extend(batch)
        if len(batch) < page:
            return out
        start += page
    raise RuntimeError(
        f"Paged past {MAX_ROWS:,} rows without reaching the end of the table. "
        "The server is most likely ignoring the range parameter."
    )


@app.get("/api/insights")
def insights(token: str = Depends(get_current_user_token)):
    """
    Everything the dashboard needs, from one pass over the table.

    Previously the page made three separate calls that each read all 3,500 rows.
    The aggregates all derive from the same scan, so doing it once is both
    faster and impossible to make inconsistent — two panels can no longer
    disagree because they read the table a second apart.

    Read through the user's own token, so RLS governs this the same way it
    governs the raw rows.
    """
    rows = _fetch_all(
        get_user_client(token),
        "id,ad_budget,budget_tier,num_leads,leads_answered,ltv_months,cumulative_profit,"
        "customer_acquisition_cost,"
        "purchased,upsell,referred,closed,not_closed,calls_to_closed,calls_to_not_closed,"
        "followup_1,followup_2,followup_3,followup_4,followup_5",
    )
    return {
        # A company that signed up a minute ago has an empty table, and every
        # aggregate below is undefined for it. Saying so once, here, is what
        # lets the dashboard show a "upload your first file" screen instead of
        # a page of dashes — and stops each panel guessing separately.
        "empty": not rows,
        "summary": _summary(rows),
        "budget": _budget(rows),
        "followup": _followup(rows),
        # Every campaign, not a sample. The picker used to be wired to the
        # ten-row endpoint built on day one to prove the database connection,
        # which quietly limited scoring to 0.3% of the data — and to the ten
        # lowest ids, so all of them were small. Four fields per row keeps the
        # whole index under a few hundred KB, and it costs no extra scan.
        # Enough fields for the picker to filter on how a campaign performed,
        # not only on what it cost. Derived rates are computed in the browser so
        # the payload stays raw and one definition of each rate lives in one place.
        "records": [{
            "id": r["id"], "budget": r["ad_budget"], "tier": r["budget_tier"],
            "leads": r["num_leads"], "answered": r["leads_answered"],
            "closed": r["closed"], "cac": r["customer_acquisition_cost"],
            "calls": r["calls_to_closed"],
        } for r in rows],
    }


def _mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _summary(rows) -> dict:
    total = len(rows)
    tiers: dict[str, int] = {}
    for r in rows:
        tiers[r["budget_tier"]] = tiers.get(r["budget_tier"], 0) + 1

    def rate(field):
        return round(100 * sum(1 for r in rows if r[field]) / total, 1) if total else 0

    return {
        "total": total,
        "avgLtvMonths": _mean(r["ltv_months"] for r in rows),
        "avgProfit": _mean(r["cumulative_profit"] for r in rows),
        "purchaseRate": rate("purchased"),
        "upsellRate": rate("upsell"),
        "referralRate": rate("referred"),
        "tiers": [{"tier": t, "count": tiers.get(t, 0)} for t in ("Low", "Mid", "High")],
        "missing": {
            "ltvMonths": sum(1 for r in rows if r["ltv_months"] is None),
            "cumulativeProfit": sum(1 for r in rows if r["cumulative_profit"] is None),
        },
    }


def _budget(rows) -> dict:
    """
    Package 6: the observed return at each budget level, plus what a fixed pot
    would earn spent entirely at one level.

    The strategies use observed averages rather than a model on purpose. At the
    moment a budget is set, none of the funnel columns exist yet — a model
    reading them would answer a question no planner can ask.
    """
    by_budget: dict[int, list[float]] = {}
    for r in rows:
        if r["cumulative_profit"] is not None:
            by_budget.setdefault(r["ad_budget"], []).append(r["cumulative_profit"])

    curve = []
    for budget in sorted(by_budget):
        profits = by_budget[budget]
        avg = sum(profits) / len(profits)
        curve.append({
            "budget": budget,
            "campaigns": len(profits),
            "avgProfit": round(avg),
            "returnPerShekel": round(avg / budget, 2),
            "tier": "Low" if budget <= 1500 else ("Mid" if budget <= 5000 else "High"),
        })

    # The pot is what the agency actually spent across every campaign in the
    # book. An invented round number made the totals look like a forecast about
    # real money when they were a hypothetical; this makes the comparison a
    # genuine "same money, spent differently".
    pot = sum(r["ad_budget"] for r in rows)
    # Two normal states of the product reach here with nothing to divide by:
    # a company on its first day (no rows), and a company whose first upload
    # has no profit column filled in yet. Neither is an error, so both return
    # the empty shape rather than raising.
    if not curve or not pot:
        return {
            "pot": pot,
            "potSource": "total ad spend across all campaigns in the dataset",
            "curve": curve, "strategies": [],
            "current": None, "best": None, "gainPct": None,
        }

    strategies = [{
        "label": f"All at {c['budget']:,}",
        "budgetEach": c["budget"],
        "campaigns": pot // c["budget"],
        "expectedProfit": round((pot // c["budget"]) * c["avgProfit"]),
        "returnPerShekel": c["returnPerShekel"],
        "tier": c["tier"],
    } for c in curve]

    # The comparison point is the agency's existing spending mix, not a
    # strawman: each level weighted by the share of total spend it holds today.
    weights = {c["budget"]: 0.0 for c in curve}
    for r in rows:
        if r["ad_budget"] in weights:
            weights[r["ad_budget"]] += r["ad_budget"] / pot
    current = sum(s["expectedProfit"] * weights[s["budgetEach"]] for s in strategies)

    best = max(strategies, key=lambda s: s["expectedProfit"])
    return {
        "pot": pot,
        "potSource": "total ad spend across all campaigns in the dataset",
        "curve": curve,
        "strategies": sorted(strategies, key=lambda s: -s["expectedProfit"]),
        "current": {"expectedProfit": round(current),
                    "returnPerShekel": round(current / pot, 2)},
        "best": best,
        # A book of campaigns that broke even overall leaves nothing to
        # express the improvement as a percentage of.
        "gainPct": (round((best["expectedProfit"] - current) / current * 100)
                    if current else None),
    }


def _followup(rows) -> dict:
    """
    Package 5: the follow-up paradox, including the test that failed to explain
    it away. Both are returned — the raw pattern alone reads as "stop calling",
    which is the conclusion the quality-controlled figures rule out.
    """
    stages = [("Leads", "num_leads"), ("Answered", "leads_answered"),
              ("Follow-up 1", "followup_1"), ("Follow-up 2", "followup_2"),
              ("Follow-up 3", "followup_3"), ("Follow-up 4", "followup_4"),
              ("Follow-up 5", "followup_5"), ("Closed", "closed")]
    top = sum(r["num_leads"] for r in rows) or 1
    funnel = [{"stage": label,
               "count": sum(r[col] for r in rows),
               "pctOfLeads": round(100 * sum(r[col] for r in rows) / top, 1)}
              for label, col in stages]
    # These are sums over every campaign, not one campaign's figures. Saying so
    # in the payload keeps the dashboard from having to remember it.
    campaigns = len(rows)

    closed = [r for r in rows if r["closed"] > 0 and r["cumulative_profit"] is not None]
    by_calls: dict[int, list[dict]] = {}
    for r in closed:
        by_calls.setdefault(r["calls_to_closed"], []).append(r)
    calls = [{
        "calls": c,
        "campaigns": len(g),
        "avgProfit": round(sum(r["cumulative_profit"] for r in g) / len(g)),
        "avgLtv": _mean(r["ltv_months"] for r in g),
        "upsellRate": round(100 * sum(1 for r in g if r["upsell"]) / len(g), 1),
    } for c, g in sorted(by_calls.items()) if len(g) >= 10]

    # Answer rate is fixed before any follow-up policy applies, so it separates
    # "these were worse leads" from "the calls did it".
    graded = sorted(closed, key=lambda r: r["leads_answered"] / max(r["num_leads"], 1))
    q = len(graded) // 4
    bands = [("Worst 25%", graded[:q]), ("Low-mid", graded[q:2 * q]),
             ("High-mid", graded[2 * q:3 * q]), ("Best 25%", graded[3 * q:])]

    def avg_profit(group):
        return round(sum(r["cumulative_profit"] for r in group) / len(group)) if group else None

    quality = []
    ratios = []
    for name, group in bands:
        fast = [r for r in group if r["calls_to_closed"] <= 2]
        mid = [r for r in group if 3 <= r["calls_to_closed"] <= 4]
        slow = [r for r in group if r["calls_to_closed"] >= 5]
        quality.append({"band": name, "fast": avg_profit(fast),
                        "medium": avg_profit(mid), "slow": avg_profit(slow)})
        if fast and slow:
            ratios.append(avg_profit(fast) / avg_profit(slow))

    # The paradox needs both ends of the call-count range to exist before it
    # can be stated. A small first upload may have only fast closers, or none
    # at all, and the honest answer there is "not enough data yet" rather than
    # a ratio invented out of one group.
    all_fast = [r for r in closed if r["calls_to_closed"] <= 2]
    all_slow = [r for r in closed if r["calls_to_closed"] >= 5]
    fast_avg, slow_avg = avg_profit(all_fast), avg_profit(all_slow)
    gap_raw = fast_avg / slow_avg if fast_avg and slow_avg else None
    gap_within = sum(ratios) / len(ratios) if ratios else None

    productive = sum(r["calls_to_closed"] * r["closed"] for r in rows)
    wasted = sum(r["calls_to_not_closed"] * r["not_closed"] for r in rows)
    call_total = productive + wasted

    return {
        "campaigns": campaigns,
        "funnel": funnel,
        "unanswered": funnel[0]["count"] - funnel[1]["count"],
        "unansweredPct": round(100 - funnel[1]["pctOfLeads"], 1),
        "byCalls": calls,
        "byQuality": quality,
        "gapRaw": round(gap_raw, 1) if gap_raw else None,
        "gapWithinQuality": round(gap_within, 1) if gap_within else None,
        "explainedByQualityPct": (round((1 - gap_within / gap_raw) * 100)
                                  if gap_raw and gap_within else None),
        "wastedCallShare": (round(100 * wasted / call_total, 1)
                            if call_total else None),
    }


@app.get("/api/me")
def me(token: str = Depends(get_current_user_token)):
    """
    Who is asking, which company they belong to, and how much data that company
    has. The profile page needs it, and so does the empty-state screen.

    Every read here goes through the caller's own token, so the answer is
    scoped by the same RLS policies as everything else: `profiles` and
    `companies` each return exactly one row — the caller's — because
    current_company_id() decides, not this function. A user whose sign-up
    trigger failed has no profile at all, and gets a 409 saying so rather than
    an empty dashboard that looks like a working account with no data.
    """
    client = get_user_client(token)
    profiles = client.table("profiles").select("user_id,email,role,created_at").execute().data
    if not profiles:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This account has no company attached to it. It was created before "
            "the workspace trigger existed, or the trigger failed.",
        )
    profile = profiles[0]
    companies = client.table("companies").select("id,name,created_at").execute().data
    company = companies[0] if companies else None

    # count="exact" asks Postgres for the total instead of counting a page of
    # rows in Python — the row cap that broke the aggregates does not apply to
    # it, and limit(1) keeps the body from carrying data nobody reads.
    records = client.table("funnel_records").select("id", count="exact").limit(1).execute()

    uploads = (
        client.table("uploads")
        .select("id,period,filename,row_count,status,error,created_at")
        .order("period", desc=True)
        .limit(24)
        .execute()
        .data
    )

    return {
        "email": profile["email"],
        "role": profile["role"],
        "joinedAt": profile["created_at"],
        "company": company,
        "recordCount": records.count or 0,
        "uploads": uploads,
    }


@app.get("/api/predict/{record_id}")
def predict_record(record_id: int, token: str = Depends(get_current_user_token)):
    """
    Score one of the company's campaigns with the company's own models.

    Nothing here falls back to a model trained on anyone else's data. That was
    the previous behaviour and it was labelled honestly in the interface, which
    is not the same as being useful: a prediction from another business's funnel
    is a number with no claim on this one.

    A target with no useful model returns null and the reason. Predicting the
    average and dressing it as a forecast would be worse than saying there is
    nothing to say yet.
    """
    client = get_user_client(token)
    rows = client.table("funnel_records").select("*").eq("id", record_id).execute().data
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such record")
    record = rows[0]

    predictions = {}
    for entry in _company_models(client):
        value = None
        if entry["useful"]:
            model = _load_model(entry)
            value = round(predict_one(model, entry["metrics"]["kind"], record), 4)
        predictions[entry["target"]] = {
            "value": value,
            "useful": entry["useful"],
            "note": entry["note"],
            "betterByPct": entry["metrics"].get("betterByPct"),
            "trainedRows": entry["trained_rows"],
            "kind": entry["metrics"]["kind"],
        }

    return {
        "id": record["id"],
        "actual": {
            "ltv_months": record["ltv_months"],
            "upsell": record["upsell"],
            "referred": record["referred"],
            "cumulative_profit": record["cumulative_profit"],
        },
        "predictions": predictions,
        "minRows": MIN_ROWS,
    }


@app.get("/api/models")
def models(token: str = Depends(get_current_user_token)):
    """
    What this company's models are and how good they are, measured on its own
    data. The predictions page leads with this rather than with a number,
    because "68% better than guessing, on your 60 campaigns" is the part that
    says whether the number below it is worth reading.
    """
    entries = _company_models(get_user_client(token))
    return {
        "minRows": MIN_ROWS,
        "models": [{
            "target": e["target"],
            "useful": e["useful"],
            "note": e["note"],
            "trainedRows": e["trained_rows"],
            "kind": e["metrics"]["kind"],
            "error": e["metrics"].get("error"),
            "baseline": e["metrics"].get("baseline"),
            "betterByPct": e["metrics"].get("betterByPct"),
            "trainedAt": e["created_at"],
        } for e in entries],
    }


def _company_models(client) -> list[dict]:
    """
    The newest version of each target. RLS restricts the read to the caller's
    own company, so no filter here decides that.
    """
    rows = (client.table("model_registry")
            .select("target,version,trained_rows,metrics,useful,note,created_at,model_b64")
            .order("version", desc=True).execute().data)
    newest: dict[str, dict] = {}
    for row in rows:
        newest.setdefault(row["target"], row)
    return list(newest.values())


# A loaded CatBoost model, kept between requests. Deserialising costs more than
# the prediction does, and a company scoring several campaigns in a row would
# otherwise pay it every time. Keyed by the stored bytes, so a retrained model
# is a different key and can never be served from the old entry.
@lru_cache(maxsize=32)
def _load_model_cached(model_b64: str, kind: str):
    return load(model_b64, kind)


def _load_model(entry: dict):
    return _load_model_cached(entry["model_b64"], entry["metrics"]["kind"])


# =====================================================================
# UPLOADS
# =====================================================================
# The product is an empty engine until a company can put its own data in it.
# Everything here writes through the caller's own token, so the WITH CHECK
# clause on funnel_records is the thing that decides a row's owner — this code
# proposes a company_id and the database is free to refuse it.


def _company_id(client) -> str:
    profiles = client.table("profiles").select("company_id").execute().data
    if not profiles:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This account has no company attached to it, so there is nowhere to "
            "put the data. Run migrations/003_backfill_profiles.sql.",
        )
    return profiles[0]["company_id"]


def _read_csv(upload: UploadFile) -> pd.DataFrame:
    """
    The bytes a browser sent, as a DataFrame — or a 400 saying why not.

    Two encodings are tried because both are normal. Excel on a Hebrew Windows
    machine writes cp1255, and a UTF-8 export from almost anything else carries
    a BOM that utf-8-sig strips and plain utf-8 turns into a corrupted first
    header. Guessing wrong here renames a column and the file is rejected for
    the wrong reason entirely.
    """
    data = upload.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"הקובץ גדול מ־{MAX_BYTES // 1024 // 1024}MB.",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "לא התקבל קובץ.")

    last: Exception | None = None
    for encoding in ("utf-8-sig", "cp1255"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=encoding)
        except Exception as exc:
            last = exc
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"לא הצלחנו לקרוא את הקובץ כ־CSV. פרטים: {last}",
    )


def _mapping(raw: str | None) -> dict[str, str] | None:
    """
    The answers the person gave to the column questions, as JSON.

    Every value is checked against the actual file before it renames anything
    (see ingest.resolve_columns), so a malformed or hostile mapping can misname
    a column but cannot invent one.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "התאמת העמודות לא נשלחה כראוי.") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "התאמת העמודות לא נשלחה כראוי.")
    return {str(k): str(v) for k, v in parsed.items()}


@app.post("/api/uploads/preview")
def preview_upload(
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None),
    token: str = Depends(get_current_user_token),
):
    """
    Check a file and write nothing.

    Separate from the save on purpose: a company should see what is wrong with
    its export, and what the product intends to store, before anything lands in
    its workspace. The browser keeps the file and sends it again to save, so
    there is no half-finished upload sitting on the server between the two
    steps waiting to be cleaned up.
    """
    get_user_client(token)  # rejects a bad token before any parsing work
    report, _ = inspect(_read_csv(file), _mapping(mapping))
    return report.as_dict()


@app.post("/api/uploads")
def create_upload(
    file: UploadFile = File(...),
    period: str = Form(...),
    mapping: str | None = Form(default=None),
    token: str = Depends(get_current_user_token),
):
    """
    Store one month of campaigns.

    The file is validated again rather than trusting the preview: the preview
    is a courtesy to the person, not a permission the browser holds. Nothing
    stops a caller skipping it.
    """
    try:
        month = date.fromisoformat(period if len(period) > 7 else period + "-01")
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "תאריך לא תקין. הפורמט הוא YYYY-MM."
        ) from None

    client = get_user_client(token)
    company_id = _company_id(client)

    # Re-uploading a month is refused rather than merged or silently doubled.
    # Both of those produce a table nobody can reason about; deleting the month
    # first is one extra click and leaves an obvious trail in the history.
    existing = (client.table("uploads").select("id,filename")
                .eq("period", month.isoformat()).execute().data)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"כבר קיימת העלאה לחודש הזה ({existing[0].get('filename') or 'ללא שם'}). "
            "מחק אותה קודם מהיסטוריית ההעלאות, ואז העלה מחדש.",
        )

    report, clean = inspect(_read_csv(file), _mapping(mapping))
    if not report.ok:
        # 422: the request was well-formed and the file inside it was not.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"message": "הקובץ לא עבר בדיקה", **report.as_dict()})

    upload = client.table("uploads").insert({
        "company_id": company_id,
        "period": month.isoformat(),
        "filename": file.filename,
        "row_count": report.rows,
        "status": "analysing",
    }).execute().data[0]

    try:
        records = to_records(clean, company_id, upload["id"])
        for i in range(0, len(records), 500):
            client.table("funnel_records").insert(records[i:i + 500]).execute()
    except Exception as exc:
        # A half-written month left marked "ready" would be presented as a
        # complete picture. Recording the failure, and removing whatever did
        # land, is what keeps the dashboard honest about what it is describing.
        client.table("funnel_records").delete().eq("upload_id", upload["id"]).execute()
        client.table("uploads").update(
            {"status": "failed", "error": str(exc)[:500]}
        ).eq("id", upload["id"]).execute()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "השמירה נכשלה באמצע והשורות שכן נשמרו הוסרו. אפשר לנסות שוב.",
        ) from exc

    client.table("uploads").update({"status": "ready"}).eq("id", upload["id"]).execute()
    models = _retrain(client, company_id, upload["id"])
    return {"uploadId": upload["id"], "period": month.isoformat(),
            "models": models, **report.as_dict()}


def _retrain(client, company_id: str, upload_id: str | None) -> list[dict]:
    """
    Fit this company's models on everything it has uploaded, not just the new
    month, and file the result.

    Measured on a full-size dataset this takes under five seconds even at the
    50,000-row upload cap, which is what makes it a step inside the request
    rather than a background job. A background job would need a queue, a status
    to poll, and a way to describe a workspace whose data and models disagree —
    all of it to save four seconds, once a month.

    A failure here does not fail the upload. The rows are already stored and the
    descriptive half of the product works without any model at all; losing the
    month's data because a model would not fit would be the wrong trade.
    """
    try:
        rows = _fetch_all(client, "*")
        trained = train(rows)
    except Exception:
        log.exception("training failed for company %s", company_id)
        return []

    out = []
    for model in trained:
        previous = (client.table("model_registry").select("version")
                    .eq("target", model.target).order("version", desc=True)
                    .limit(1).execute().data)
        client.table("model_registry").insert({
            "company_id": company_id,
            "upload_id": upload_id,
            "target": model.target,
            "version": (previous[0]["version"] + 1) if previous else 1,
            "trained_rows": model.rows,
            "metrics": {"error": model.score, "baseline": model.baseline,
                        "betterByPct": model.better_by_pct, "kind": model.kind},
            "model_b64": base64.b64encode(model.model_bytes).decode(),
            "useful": model.useful,
            "note": model.note,
        }).execute()
        out.append({"target": model.target, "rows": model.rows,
                    "betterByPct": model.better_by_pct, "useful": model.useful,
                    "note": model.note})
    return out


@app.patch("/api/company")
def rename_company(payload: dict, token: str = Depends(get_current_user_token)):
    """
    Rename the caller's own workspace.

    Nothing here checks which company is being renamed, and that is deliberate:
    the UPDATE policy added in 004 restricts the statement to the caller's own
    row, so an id supplied by a caller cannot widen what the update touches.
    """
    name = str(payload.get("name", "")).strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "שם החברה חייב להיות בין תו אחד ל־120 תווים.")
    client = get_user_client(token)
    updated = (client.table("companies").update({"name": name})
               .eq("id", _company_id(client)).execute().data)
    if not updated:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "אין הרשאה לשנות את שם החברה.")
    return updated[0]


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str, token: str = Depends(get_current_user_token)):
    """
    Undo one month.

    The rows go with it. funnel_records has no UPDATE policy by design —
    correcting a month means deleting it and uploading again, which leaves a
    record of both actions instead of quietly changing history in place.
    """
    client = get_user_client(token)
    found = client.table("uploads").select("id").eq("id", upload_id).execute().data
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "אין העלאה כזו.")

    removed = client.table("funnel_records").delete().eq("upload_id", upload_id).execute()
    client.table("uploads").delete().eq("id", upload_id).execute()
    # The models were fitted on rows that no longer exist. Leaving them in place
    # would keep predicting from a month the company deliberately withdrew.
    company_id = _company_id(client)
    client.table("model_registry").delete().eq("company_id", company_id).execute()
    models = _retrain(client, company_id, None)
    return {"deleted": upload_id, "rowsRemoved": len(removed.data), "models": models}


# Mounted LAST on purpose: a mount at "/" catches every path the routes above
# did not claim, so declaring it earlier would shadow the whole API.
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

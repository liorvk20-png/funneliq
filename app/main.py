from dotenv import load_dotenv

# Populate the environment from .env before app.auth / app.db read it at import
# time. On Railway there is no .env file and this is a harmless no-op — the
# platform supplies the same variables directly.
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, status  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.auth import get_current_user_token  # noqa: E402
from app.config import require_env  # noqa: E402
from app.db import get_user_client  # noqa: E402
from app.predict import predict  # noqa: E402

app = FastAPI(title="FunnelIQ")


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
        "gainPct": round((best["expectedProfit"] - current) / current * 100),
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

    all_fast = [r for r in closed if r["calls_to_closed"] <= 2]
    all_slow = [r for r in closed if r["calls_to_closed"] >= 5]
    gap_raw = avg_profit(all_fast) / avg_profit(all_slow)
    gap_within = sum(ratios) / len(ratios)

    productive = sum(r["calls_to_closed"] * r["closed"] for r in rows)
    wasted = sum(r["calls_to_not_closed"] * r["not_closed"] for r in rows)

    return {
        "campaigns": campaigns,
        "funnel": funnel,
        "unanswered": funnel[0]["count"] - funnel[1]["count"],
        "unansweredPct": round(100 - funnel[1]["pctOfLeads"], 1),
        "byCalls": calls,
        "byQuality": quality,
        "gapRaw": round(gap_raw, 1),
        "gapWithinQuality": round(gap_within, 1),
        "explainedByQualityPct": round((1 - gap_within / gap_raw) * 100),
        "wastedCallShare": round(100 * wasted / (wasted + productive), 1),
    }


@app.get("/api/predict/{record_id}")
def predict_record(record_id: int, token: str = Depends(get_current_user_token)):
    """
    Score one stored campaign with all three models.

    The record is fetched through the user's own token, so a caller who cannot
    read a row cannot get a prediction about it either — the gate is the same
    one that guards the data, not a second one bolted on beside it.
    """
    client = get_user_client(token)
    rows = client.table("funnel_records").select("*").eq("id", record_id).execute().data
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such record")
    record = rows[0]
    return {
        "id": record["id"],
        "actual": {
            "ltvMonths": record["ltv_months"],
            "upsell": record["upsell"],
            "referred": record["referred"],
            "cumulativeProfit": record["cumulative_profit"],
        },
        "predicted": predict(record),
    }


# Mounted LAST on purpose: a mount at "/" catches every path the routes above
# did not claim, so declaring it earlier would shadow the whole API.
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

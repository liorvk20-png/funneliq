from dotenv import load_dotenv

# Populate the environment from .env before app.auth / app.db read it at import
# time. On Railway there is no .env file and this is a harmless no-op — the
# platform supplies the same variables directly.
load_dotenv()

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.auth import get_current_user_token  # noqa: E402
from app.config import require_env  # noqa: E402
from app.db import get_user_client  # noqa: E402

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
    while True:
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


@app.get("/api/summary")
def summary(token: str = Depends(get_current_user_token)):
    """
    Headline figures for the dashboard. Read through the user's own token, so
    an unauthorised caller gets nothing here for the same reason they get
    nothing from the sample endpoint — RLS, not application logic.

    Aggregated in Python rather than SQL: 3,500 rows is small, and keeping the
    arithmetic here means the numbers on the dashboard come from the same
    place the analysis packages will read, with no second definition of a
    metric to drift out of sync.
    """
    client = get_user_client(token)
    rows = _fetch_all(
        client,
        "ad_budget,budget_tier,ltv_months,cumulative_profit,purchased,upsell,referred",
    )

    def mean(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    total = len(rows)
    tiers = {}
    for r in rows:
        tiers[r["budget_tier"]] = tiers.get(r["budget_tier"], 0) + 1

    def rate(field):
        return round(100 * sum(1 for r in rows if r[field]) / total, 1) if total else 0

    return {
        "total": total,
        "avgLtvMonths": mean(r["ltv_months"] for r in rows),
        "avgProfit": mean(r["cumulative_profit"] for r in rows),
        "purchaseRate": rate("purchased"),
        "upsellRate": rate("upsell"),
        "referralRate": rate("referred"),
        "tiers": [{"tier": t, "count": tiers.get(t, 0)} for t in ("Low", "Mid", "High")],
        "missing": {
            "ltvMonths": sum(1 for r in rows if r["ltv_months"] is None),
            "cumulativeProfit": sum(1 for r in rows if r["cumulative_profit"] is None),
        },
    }


# Mounted LAST on purpose: a mount at "/" catches every path the routes above
# did not claim, so declaring it earlier would shadow the whole API.
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

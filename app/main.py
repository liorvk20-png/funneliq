from dotenv import load_dotenv

# Populate the environment from .env before app.auth / app.db read it at import
# time. On Railway there is no .env file and this is a harmless no-op — the
# platform supplies the same variables directly.
load_dotenv()

from fastapi import Depends, FastAPI  # noqa: E402

from app.auth import get_current_user_token  # noqa: E402
from app.db import get_user_client  # noqa: E402

app = FastAPI(title="FunnelIQ")


@app.get("/health")
def health():
    """Public, no auth. Confirms the service is up — this is the one Railway checks."""
    return {"status": "ok"}


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


# The static/ dashboard (login screen + panels) mounts here once it exists —
# left out of the day-1 skeleton on purpose. Add in Week 1:
#
# from fastapi.staticfiles import StaticFiles
# app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

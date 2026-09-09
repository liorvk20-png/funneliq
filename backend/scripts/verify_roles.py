"""
Do the three roles actually do three different things?

Written after /api/me returned the wrong person's row. A colleague may
legitimately read every profile in their company -- that is what makes the
members list possible -- and taking the first row returned whichever one
Postgres handed back. With one member per company it was always right; with two
it was usually the admin's, and an editor was shown the admin's name, the
admin's role and the admin's controls.

Every check here needs two people in one workspace, which is why nothing caught
it earlier.

    uvicorn app.main:app --port 8899        # in another terminal
    python scripts/verify_roles.py
"""
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv()

from simulate_customer_journey import (  # noqa: E402
    ADMIN,
    URL,
    campaigns,
    settle,
    to_csv,
)

API = "http://127.0.0.1:8899"
made, fails = [], []

def check(name, ok, detail=""):
    if not ok:
        fails.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'   ' + detail if detail else ''}")

def token(email, pw="Secret123!"):
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers=ADMIN,
                      json={"email": email, "password": pw}).json()
    return {"Authorization": "Bearer " + r["access_token"]}

def signup(**kw):
    body = {"password": "Secret123!", "passwordConfirm": "Secret123!",
            "fullName": "דנה כהן", "jobTitle": "מנהלת מדיה", "phone": "0521234567",
            "gender": "female", "birthYear": "1990", "requestedRole": "editor"}
    body.update(kw)
    r = requests.post(f"{API}/api/signup", json=body)
    if r.status_code == 200:
        made.append(body["email"])
    return r

try:
    print("A — the details reach the profile")
    owner = f"own-{uuid.uuid4().hex[:8]}@funneliq.test"
    signup(email=owner, company="סוכנות בדיקה", fullName="ליאור מנהל",
           jobTitle="מנכ״ל", requestedRole="admin")
    a = token(owner)
    me = requests.get(f"{API}/api/me", headers=a).json()
    check("full name stored", me.get("fullName") == "ליאור מנהל", str(me.get("fullName")))
    check("job title stored", me.get("jobTitle") == "מנכ״ל", str(me.get("jobTitle")))
    check("workspace opener is an admin", me.get("role") == "admin")
    check("and may both edit and manage", me.get("canEdit") and me.get("canManage"))

    print("\nB — a requested role is a request, not a grant")
    other = f"req-{uuid.uuid4().hex[:8]}@funneliq.test"
    signup(email=other, company="סוכנות אחרת", requestedRole="viewer")
    o = token(other)
    check("asking for viewer still administers your own workspace",
          requests.get(f"{API}/api/me", headers=o).json().get("role") == "admin")

    print("\nC — an editor edits data and manages nobody")
    inv = requests.post(f"{API}/api/seats", headers=a,
                        json={"email": "ed@funneliq.test", "role": "editor"})
    check("admin can open an editor seat", inv.status_code == 200, str(inv.status_code))
    ed_email = f"ed-{uuid.uuid4().hex[:8]}@funneliq.test"
    # the invitation is bound to one address, so re-issue it to the real one
    requests.delete(f"{API}/api/seats/{inv.json()['invitation_id']}", headers=a)
    inv = requests.post(f"{API}/api/seats", headers=a,
                        json={"email": ed_email, "role": "editor"}).json()
    signup(email=ed_email, invitationCode=inv["code"], company="")
    e = token(ed_email)
    ed_me = requests.get(f"{API}/api/me", headers=e).json()
    check("the invitee joined the same workspace",
          ed_me["company"]["name"] == "סוכנות בדיקה", str(ed_me["company"]["name"]))
    check("as an editor", ed_me.get("role") == "editor", str(ed_me.get("role")))
    check("who may edit", ed_me.get("canEdit") is True)
    check("and may not manage", ed_me.get("canManage") is False)
    up = requests.post(f"{API}/api/uploads", headers=e,
                       files={"file": ("m.csv", to_csv(campaigns(15, 4)))},
                       data={"period": "2026-03"})
    check("an editor can import data", up.status_code == 200, str(up.status_code))
    if up.status_code == 200:
        settle(e, up.json()["uploadId"])
    check("an editor cannot invite anyone",
          requests.post(f"{API}/api/seats", headers=e,
                        json={"email": "x@funneliq.test", "role": "viewer"}).status_code == 403)

    print("\nD — a viewer sees everything and changes nothing")
    vw_email = f"vw-{uuid.uuid4().hex[:8]}@funneliq.test"
    vinv = requests.post(f"{API}/api/seats", headers=a,
                         json={"email": vw_email, "role": "viewer"}).json()
    signup(email=vw_email, invitationCode=vinv["code"], company="")
    v = token(vw_email)
    vm = requests.get(f"{API}/api/me", headers=v).json()
    check("joined as a viewer", vm.get("role") == "viewer", str(vm.get("role")))
    check("sees the same data",
          requests.get(f"{API}/api/insights", headers=v).json()["summary"]["total"] == 15,
          str(requests.get(f"{API}/api/insights", headers=v).json()["summary"]["total"]))
    vup = requests.post(f"{API}/api/uploads", headers=v,
                        files={"file": ("m.csv", to_csv(campaigns(5, 9)))},
                        data={"period": "2026-04"})
    check("a viewer cannot import", vup.status_code != 200, f"HTTP {vup.status_code}")
    check("a viewer cannot rename the company",
          requests.patch(f"{API}/api/company", headers=v,
                         json={"name": "נחטף"}).status_code != 200)
    check("the name is unchanged",
          requests.get(f"{API}/api/me", headers=a).json()["company"]["name"] == "סוכנות בדיקה")

    print("\nE — the workspace lists people")
    seats = requests.get(f"{API}/api/seats", headers=a).json()
    check("three members", len(seats["members"]) == 3, str(len(seats["members"])))
    check("each with a name and a role",
          all(m.get("full_name") and m.get("role") for m in seats["members"]))
    exposed = set(seats["members"][0])
    check("and nothing more than needed",
          exposed == {"user_id", "email", "role", "created_at", "full_name", "job_title"},
          str(sorted(exposed)))

    print("\nF — nobody can promote themselves")
    from app.db import get_user_client
    tok = token(vw_email)["Authorization"].split()[1]
    try:
        rows = get_user_client(tok).table("profiles").update({"role": "admin"}) \
            .eq("email", vw_email).execute().data
        check("a viewer cannot make themselves an admin", not rows, f"{len(rows)} rows changed")
    except Exception:
        check("a viewer cannot make themselves an admin", True)
    check("still a viewer",
          requests.get(f"{API}/api/me", headers=v).json()["role"] == "viewer")

finally:
    users = requests.get(f"{URL}/auth/v1/admin/users?per_page=200", headers=ADMIN).json()["users"]
    for u in users:
        if u["email"] in made:
            requests.delete(f"{URL}/auth/v1/admin/users/{u['id']}", headers=ADMIN)
    from app.db import get_service_client
    c = get_service_client()
    kept = {p["company_id"] for p in c.table("profiles").select("company_id").execute().data}
    for comp in c.table("companies").select("id").execute().data:
        if comp["id"] not in kept:
            c.table("companies").delete().eq("id", comp["id"]).execute()
    print("\ncleaned up")

print("\n" + "=" * 56)
print("all checks passed" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

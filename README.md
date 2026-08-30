# FunnelIQ

Marketing analytics for Northbound Media. Turns 3,500 rows of funnel data into
answers a person can act on, served as a login-gated web app.

**Live:** https://web-production-ff4c1.up.railway.app

## What it answers

| # | Question | Result |
|---|----------|--------|
| 1 | What is actually in the data? | Budget tier is non-monotonic; 39% of leads never answer |
| 2 | How long will a customer stay? | CatBoost, **2.18 months MAE** against 11.43 for guessing the mean |
| 3 | Who will buy an upsell? | CatBoost, **0.818 recall**, 0.680 PR-AUC |
| 4 | Who is worth chasing? | Calibrated 0–100 score, 9.3% → 73.3% referral rate across bands |
| 5 | Do follow-up calls destroy value? | The "it's just bad leads" explanation **fails its own test** |
| 6b | What drove a campaign's profit? | CatBoost, MAE 2,462 — **explanatory only**, 97.7% of it needs data a planner lacks |
| 6 | Where should the budget go? | Return peaks at **10.87x** and falls below 1.00x above 6,000 |

The budget simulator compares strategies over the agency's **actual** total ad
spend of 16,293,700, not an invented round number, so "299% more" means the same
money spent differently. The ratio is the finding; it holds at any budget size.

Full numbers, decisions and caveats: [FINDINGS.md](FINDINGS.md).

## Architecture

```
Browser ──1. email + password──▶ Supabase Auth ──▶ signed JWT
   │
   └──2. request + JWT──▶ FastAPI on Railway ──3. same JWT──▶ Postgres
                                                              (RLS decides)
```

Every read carries the signed-in user's own token, so Row Level Security governs
access at the database. The application never decides who may see what, and the
secret key — which would bypass RLS — is never sent to a browser and is not
needed by the running service at all.

## Layout

```
app/            the web service
  main.py       routes; the static mount is declared last so it cannot shadow the API
  auth.py       verifies JWTs against the project's public JWKS (ES256/RS256)
  db.py         two clients: user-scoped (RLS applies) and secret (scripts only)
  predict.py    loads the three CatBoost models once at startup
  static/       login screen and dashboard, no build step
analysis/       one script per work package, each reproducible on its own
  features.py   the single feature definition; enforces the leakage rule by construction
models/         trained artifacts, committed so a deploy need not retrain
scripts/        CSV loader
migrations/     schema history; 002 turns one company into an engine anyone can join
```

## Running it locally

```bash
python3.13 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # server
pip install -r requirements-ml.txt       # training as well

cp .env.example .env                     # then fill in the two required values
uvicorn app.main:app --reload
```

`.env` needs `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. `SUPABASE_SECRET_KEY`
is required only by the loader script; the web service runs without it, and on a
public server it is better left unset.

Missing configuration fails at startup with a message naming the variable and
where it belongs, rather than a bare `KeyError`.

## Reproducing the analysis

Each script is standalone and reads the CSV rather than the database, so it
describes the raw input regardless of the table's current state.

```bash
python analysis/eda.py                      # Package 1
python analysis/package2_ltv.py             # Package 2  (writes models/)
python analysis/package3_upsell.py          # Package 3
python analysis/package4_super_customer.py  # Package 4  (tuning, a few minutes)
python analysis/package5_followup.py        # Package 5
python analysis/package6_budget.py          # Package 6
```

## Loading the data

```bash
python scripts/load_csv_to_supabase.py funnel_marketing_data.csv
```

Clears the table and reloads, so re-running never duplicates. Requires
`SUPABASE_SECRET_KEY`. Expect `Loaded 3500 rows`.

## Isolation between companies

Every company's data is invisible to every other company, and that is enforced
by Postgres rather than by application code. Each policy compares against
`current_company_id()`, which reads the caller's profile, so a forgotten filter
or a hastily written endpoint cannot leak another company's rows — the database
refuses first.

Signing up creates a company and a profile through a trigger on `auth.users`.
Without it a user would authenticate successfully and then see nothing, because
`current_company_id()` returns NULL and every policy fails closed.

```bash
python scripts/verify_isolation.py
```

Creates two throwaway companies, gives each a row, checks that neither can read
or write the other's, and removes both. It runs against the real database on
purpose: the thing under test is a Postgres policy, and only Postgres can say
whether it holds.

## The rules this project holds itself to

**No data leakage.** `upsell`, `referred`, `cumulative_profit` and `ltv_months`
are outcomes of the funnel. None is ever a feature when predicting another —
`features.py` removes them by construction rather than by remembering. They
correlate up to 0.85, so the rule is not theoretical.

**Measure, don't assert.** Every judgement call in FINDINGS.md was decided by
running both options. `purchased` was dropped from the LTV model and kept for
upsell because that is what the numbers said in each case, not because a list
permitted it.

**Report what happened.** Package 5 was written expecting to explain the
follow-up effect away as lead quality. It failed, and it is written up as it
came out. Package 4's hyperparameter search bought +0.0014 and says so.

**Caveats travel with the number.** The LTV endpoint returns its ±2.2 month
cross-validated error; scores above 80 carry the overconfidence flag Package 4
measured. A caveat in a document nobody opens is not a caveat.

## Notes

- Python 3.13 in all three places: local, CI, and Railway (`runtime.txt`).
- CI lints `app`, `scripts`, `analysis` and `tests`, then runs the test suite, on
  every push and pull request.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests -q
```

51 tests, no secrets and no network — everything reads the committed CSV or
feeds hand-built rows through the same functions the API uses.

Most of them exist because a specific bug got through. The ones worth knowing:

| test | the bug it pins down |
|------|----------------------|
| `test_pagination.py` | PostgREST silently caps a response at 1,000 rows; the first summary averaged 1,000 of 3,500 and looked entirely plausible |
| `test_loader.py` | `df.where(pd.notnull(df), None)` leaves NaN on a float column, and `json.dumps` emits a bare `NaN` rather than raising |
| `test_aggregates.py` | the simulator's pot was an invented 1,000,000 presented as a finding |
| `test_leakage.py` | the four outcome columns must never be features for one another |
| `test_tiers.py` | `budget_tier` is defined in three places and they have to agree |

Each was verified by reintroducing the bug and watching the suite fail. A test
that does not fail when the code breaks is worse than no test.
- Only `catboost` ships to production. The three winning models are all CatBoost,
  so xgboost, lightgbm and scikit-learn stay in `requirements-ml.txt`.
- `models/` is committed. The artifacts are 1.4MB and never change between
  deploys; training at build time would mean shipping the full training stack.

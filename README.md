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
migrations/     schema history; 002 turns one company into an engine anyone can join,
                003 backfills workspaces for accounts that predate the trigger,
                004 lets a company rename itself, 005 stores its own models,
                006 stores its logo, 007 adds findings and narrative,
                008 adds seats and invitations
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

## Who the interface is written for

Somebody who runs a marketing department, and has not asked what LTV stands
for. Every figure carries its own sentence, from one glossary, so the name and
the explanation cannot drift apart — "כמה זמן לקוח נשאר" is followed by "כמה
חודשים בממוצע לקוח ממשיך לשלם לפני שהוא עוזב", because the field name never
answered that and the question was asked twice.

Nothing describing how the models work appears on screen any more: no error
score, no comparison against guessing, no row counts. Those describe our
problem. What is left is one sentence saying whether a prediction can be
relied on, and the predictions that can — a target that did not beat guessing
shows no number at all, because a greyed-out figure with a caveat beside it
still reads as a prediction.

The campaigns table shows every campaign, sortable, filterable and searchable,
a page at a time. It replaced a fixed sample of ten rows, which answered no
question anyone has: you could not find your worst campaign in it, could not
check the one you remembered, and could not tell whether the ten were
representative. They were the ten oldest.

Charts are hand-drawn SVG — four functions rather than a hundred kilobytes of
library and a supply chain. Each carries a text alternative, because a picture
of a number is useless to a screen reader unless the number is written down too.

"מה כדאי לעשות" is the page the rest of the product exists to fill. Every other
page says what happened; that one says what to change, ordered by how much it is
worth, with the figure behind each suggestion shown underneath it. Nothing there
is invented in the browser: an item disappears when its number does, rather than
degrading into a generic tip.

## The interface

One HTML file, no build step and no framework. Everything visual comes from the
token block at the top of `app/static/index.html`: two themes defined as the
same names with different values, so no rule below that block knows which theme
it is drawing.

The theme is set by four lines in the head, before the stylesheet paints,
because deciding it in the main script at the end of the body shows every
dark-mode user a white flash on every load. The palette is written a second
time under `prefers-color-scheme`, so a browser with no JavaScript still gets
the right one — duplicated because plain CSS cannot share a token block between
two selectors, with a test asserting the two never drift.

No native `alert` or `confirm` anywhere: both block the page, cannot be written
in Hebrew by us, and are prefixed by the browser with the host name. Messages
are toasts, and destructive actions get a real dialog with a focus trap.

`/api/me` asks for the whole company row rather than naming its columns. That
is not laziness: PostgREST rejects the entire query if one named column is
missing, and `/api/me` runs on every sign-in — naming `logo_b64` there took the
product down for every user between a deploy and its migration. A test pins it.

## Social sign-in (Google, Apple)

The code is in place and the buttons are drawn from `/api/auth/providers`,
which reads what Supabase actually has configured. Nothing is hard-coded: an
unconfigured provider shows no button, because a button that leads nowhere
fails after the person has already decided to trust it.

Turning them on is configuration, not code, and it has to be done by whoever
owns the accounts:

**Google** — free.
1. Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID
   → Web application.
2. Authorised redirect URI: `https://<project>.supabase.co/auth/v1/callback`.
3. Supabase → Authentication → Sign In / Providers → Google → paste the client
   ID and secret, enable, save.

This also covers Android: "sign in with Android" is sign in with Google.

**Apple** — needs a paid Apple Developer account (about $99/year).
1. Apple Developer → Certificates, Identifiers & Profiles → an App ID, a
   Services ID, and a Sign in with Apple key.
2. Return URL: `https://<project>.supabase.co/auth/v1/callback`.
3. Supabase → Authentication → Sign In / Providers → Apple → paste the Services
   ID, Team ID, Key ID and the key file, enable, save.

Nothing in this repository changes for either. The buttons appear on their own
once the provider is enabled, because the endpoint reports it.

## Seats

A company is no longer one person. `migrations/008_seats_and_invites.sql` adds
invitations and splits what a role may do:

- **Admin** — uploads files, deletes months, renames the company, changes the
  logo, invites and removes colleagues.
- **Viewer** — sees everything an admin sees, and changes nothing.

Reading is deliberately identical for both. A colleague given a seat can see
the whole workspace, which is the point of the seat; the difference is writing,
and it is enforced by RLS policies rather than by the interface hiding buttons.

An invitation is a code tied to one address. The sign-up trigger compares the
two, so a leaked code cannot be redeemed by anyone else, and a code that is
missing, expired, spent or issued to a different address falls through to
creating a new company rather than refusing the sign-up — someone with a
mistyped code can still register, and can always be invited again.

`public.current_member_role()` is named that way because `current_role` is a
reserved SQL keyword returning the connection's database role.

## Simulating a customer

Two scripts drive the real endpoints against the real database, the way a
company would, and check every answer against an independent calculation:

```bash
uvicorn app.main:app --port 8899          # in another terminal
python scripts/simulate_customer_journey.py
python scripts/simulate_edge_cases.py
```

Both create throwaway accounts and delete them afterwards, including on
failure. They exist because status codes were never the problem here: every bug
that reached this product returned 200 — a thousand rows averaged as if they
were three thousand, a picker wired to ten campaigns, sums across the whole
book labelled as one month. So the journey script recomputes each figure with
pandas and compares, and prints both when they differ.

The edge-case script covers what a real export contains rather than what the
code expects: three encodings, thousands separators, currency symbols, blanks,
unknown columns, duplicate rows, arithmetic that does not add up, a month of
one campaign, a month of all zeros, malformed periods, hostile column mappings,
and a stale session pointed at every endpoint.

Four defects came out of the first run and none of them were visible from the
code:

`1,200` — how Excel writes a budget — was rejected as "not a number", so a real
first upload would have failed on a file the person can see is fine.

A single zero-budget row raised ZeroDivisionError while building the return
curve, taking down every panel on the page rather than the one that could not
be computed.

A malformed `Authorization` header returned 500 instead of 401, because
base64url_decode raises `binascii.Error` before PyJWT's own exceptions, and the
token splitter raises `DecodeError`, which is not a `ValueError`. Both are now
caught — after `PyJWKClientConnectionError`, which inherits from `PyJWTError`
and would otherwise be swallowed by the same clause, turning our own outage
into "your session expired".

And the narrative said "העלות לליד יציב יחסית". עלות is feminine. Templates were
writing agreeing words literally, which is right for half the metrics and wrong
for the other half, and renders without complaint either way — the exact class
of error the gender field exists to prevent, reintroduced by templates that did
not use it. Words now agree through `{m|agree:...}`, an unlisted word drops the
rule rather than guessing, and a test fails on any template that writes one
literally.

## Findings and the narrative (WP7)

Numbers never become sentences directly. Metrics become `Finding` objects and
only findings become words, because the same objects feed the dashboard, the
alerts, the PDF and any future agent — each reads structure instead of
re-deriving meaning from rows, so the next consumer costs nothing to add.

The iron rule follows: `narrative_outputs.finding_id` is NOT NULL and a foreign
key, so a sentence with nothing behind it cannot be stored and therefore cannot
be shown.

**Contribution analysis** (`app/analytics/contribution.py`). An overall rate is
a weighted average, and when it falls two different things may have happened:
the mix moved toward weaker segments, or the segments themselves got worse. The
same headline, opposite responses. The split is exact — mix + rate +
interaction equals the change to 1e-9, asserted on hand-built, generated and
real data. The interaction term is kept rather than folded into the others:
absorbing it is how a decomposition starts lying, because it produces a
confident mix-versus-rate verdict exactly when no such verdict is available.

**Factor decomposition** (`app/analytics/factor_decomp.py`). Logs turn a
product into a sum, so CPA splits into click price and conversion rate as
additive shares. On the reference data a 28% rise came out as 61% conversion
weakness and 39% click price — the question the category is actually asked. The
factors are checked against the metric's own change and a list that does not
reconstruct it raises, because a missing term produces a plausible, wrong
attribution rather than an error.

**Evidence gates** (`app/analytics/significance.py`). Small denominators
produce the largest percentage swings, so without a gate the ranking fills with
the findings least worth reading. Rates get a two-proportion z test; ratios and
sums get a bootstrap, vectorised — the loop version was correct and took most
of sixteen seconds on a full run. Nothing is discarded for being uncertain; it
is labelled `insufficient` and shown in the quality section, because a person
who reads "too few records to conclude" trusts the product more afterwards than
one who reads a confident number and later learns what it rested on.

**Hebrew** (`app/narrative/hebrew.py`). A verb agrees with its subject, so the
same event is עלתה for עלות and עלה for שיעור — and the pair itself changes
with the kind of thing, since prices rise and fall while quantities grow and
shrink. Both are lookups over a hand-checked list. Prepositions merge with the
definite article too: ב + הרווח is ברווח, never בהרווח.

**Rules** (`app/narrative/rules_seed.py`). Forty-six: the specification's
forty-two, three correlational twins, and one for a segment moving against the
overall change. The twins exist because the guardrail on causal language would
otherwise delete an observation rather than rephrase it — causal wording needs
high confidence *and* a share of at least 0.60, enforced by conditions on the
rule so a template that reaches for "נובע מ" without them never fires.

Conditions are a closed DSL: nine comparisons against literals, no eval, no
callables. Rules are data and data may one day be edited by someone who is not
us.

### Deviations from the specification, and why

`org_id` is `company_id`. Tenancy here has been `companies` and
`current_company_id()` since 002; a second key alongside the first would mean
two isolation systems that must agree forever, and the failure mode when they
stop agreeing is one customer reading another's analysis.

pandas rather than DuckDB. At the 50,000-row ceiling an upload already
enforces, a groupby is the same operation without a second engine to install,
pin and keep working on the deploy.

Two dimensions rather than six. This data has no channel, no device and no
per-row date; it has `budget_tier`, and a volume band derived from ad-side
numbers only, so it is known before any outcome. The miner is generic over a
dimension list — when the ad-platform integrations land they join it and
nothing else changes.

### What happens after an upload

The rows are stored and the request ends. Training and the narrative run after
the response, and `uploads.status` moves from `analysing` to `ready`.

That reverses an earlier decision here, and the reason is a measurement that had
not been made. Fitting all four models takes about a second, so the first
version did it inside the request. End to end the upload took nine to
twenty-one seconds, because roughly twenty separate round trips to Supabase sit
around the arithmetic: the insert, reading the workspace back, a version lookup
per target, the model writes, the run, the findings, the sentences. Timing the
arithmetic alone gave the wrong answer.

The read is now taken once and handed to both steps, the version lookup is one
query instead of four, and the response arrives in one to seven seconds with the
dashboard already correct. The models and the narrative land a few seconds
later, and the interface says so instead of leaving a disabled button to be read
as a hang — which is how it was read.

## Models

Each company's models are fitted on that company's rows, at upload time. All
four take under five seconds even at the 50,000-row cap, which is what makes
this a step inside the request rather than a background job with a queue and a
status to poll.

What matters more than accuracy is honesty about accuracy. Every model is
scored on rows it did not see and compared against the laziest possible
alternative — predicting the average — on the same rows, and that comparison
travels with it into the interface. A model that does not beat guessing is
stored, reported, and never used: a prediction no better than the average is
not a weak answer to caveat, it is an answer we do not have. Below ten rows
nothing is fitted at all, because there the measurement of whether a model
works is itself unreliable.

Measured on the reference data, holding out a quarter: ten rows beat guessing
by 4%, twenty-five by 45%, fifty by 69%, a hundred by 74%. The classifiers take
longer to become worth anything than the regressors do — on sixty rows they
lose to the base rate — which is why the gate is a measurement per company and
not a row-count threshold.

The four downstream outcomes (`upsell`, `referred`, `cumulative_profit`,
`ltv_months`) are removed from the feature set by construction, so a new target
cannot quietly reintroduce one.

## Getting data in

Companies load their own data through the product: **העלאת נתונים** in the
sidebar takes a CSV, checks it, shows what it found, and stores nothing until
the person confirms. Each month is a separate upload that can be deleted on its
own. `funnel_marketing_data.csv` in this repo is a **test fixture**, not
product data — the test suite reads it, and no company's workspace contains it.

Columns are recognised rather than dictated. `app/mapping.py` matches whatever
the export calls things — "תקציב", "Ad Spend" and "budget" are all `ad_budget`
— and only applies a match it is confident about; anything weaker becomes a
question with three example values from that column beside it, because the
matcher just failed to decide from the name and the person should not have to
either.

The care in that file is aimed at one failure. `leads_answered` and
`leads_not_answered` differ by three characters and every similarity measure
rates each as an excellent match for the other; swapped, they load cleanly and
invert every answer rate in the product with nothing to show for it. So
negation is a veto applied before similarity is consulted, it covers words that
negate by meaning rather than spelling (`lost`, `wasted`, `אבודים`) and Hebrew's
attached prefixes (`שלא`), and twenty pairs of opposites are tested in both
directions on every commit.

`app/ingest.py` holds the rules, with no database or HTTP dependency, so what
is accepted can be tested directly. Errors block a save (a missing column, text
where a number belongs, more than 50,000 rows); warnings do not (rows whose
parts do not sum to their whole, empty outcome columns, duplicate rows).
Nothing is repaired silently: a value the company never sent would flow into
their averages with nothing marking it as ours.

## Loading the data from the command line

Rows belong to a company, so the loader has to be told which one. It takes the
email of a user in that company and looks the workspace up through `profiles`:

```bash
python scripts/load_csv_to_supabase.py --company you@example.com
```

Replaces **that company's** rows and leaves nothing else in the table touched,
so re-running never duplicates and never reaches another customer's data. Each
run records itself in `uploads`, marked `failed` rather than `ready` if the
insert breaks part-way. Requires `SUPABASE_SECRET_KEY`. Expect
`Loaded 3,500 rows into <company>`.

If it reports no profile for that email, the account predates the sign-up
trigger: run `migrations/003_backfill_profiles.sql` in the Supabase SQL editor
first. An account with no profile authenticates fine and then sees an empty
product, because `current_company_id()` returns NULL and every policy fails
closed.

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

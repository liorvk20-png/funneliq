# FunnelIQ — findings

Running record of what the data says and which calls were made. Reproduce any
number here with `python analysis/eda.py`.

## Locked rules

**Data leakage.** `upsell`, `referred`, `cumulative_profit` and `ltv_months`
are outcomes of the funnel. None may be a feature when predicting another —
only ever a target. Any exception needs a written justification in this file.

The correlations show why the rule is not theoretical:

|                   | upsell | referred | profit | ltv |
|-------------------|--------|----------|--------|-----|
| upsell            | 1.00   | 0.55     | 0.65   | 0.49 |
| referred          | 0.55   | 1.00     | 0.58   | 0.52 |
| cumulative_profit | 0.65   | 0.58     | 1.00   | 0.85 |
| ltv_months        | 0.49   | 0.52     | 0.85   | 1.00 |

A profit model handed `upsell` would score beautifully and be worthless: when a
budget decision is actually made, nobody yet knows whether the upsell happened.

## Cleaning decisions

### Missing values — left as NULL

4 rows missing `ltv_months` (0.11%), 29 missing `cumulative_profit` (0.83%).

Not imputed. XGBoost, LightGBM and CatBoost all handle missing features
natively, so filling them would invent data to solve a problem the models do
not have. A row missing the value being predicted is dropped for that model
only — 4 rows for the LTV target, 29 for the profit target, both negligible.

### Five extreme profits — kept

| budget | tier | closed | ltv_months | cumulative_profit | upsell | referred |
|--------|------|--------|------------|-------------------|--------|----------|
| 2,000  | Mid  | 3      | 53         | 110,250           | yes    | yes      |
| 1,500  | Low  | 2      | 56         | 142,808           | yes    | yes      |
| 3,000  | Mid  | 3      | 47         | 127,268           | yes    | yes      |
| 5,000  | Mid  | 4      | 49         | 149,959           | yes    | yes      |
| 3,000  | Mid  | 2      | 42         | 141,533           | no     | yes      |

The 99th percentile is 31,569 and the highest value below 100k is 34,659, so
these sit roughly four times above everything else.

Kept, because they are internally consistent rather than anomalous: every one
has an LTV of 42–56 months against a median near 30, and every one came through
a referral. They read as genuine long-lived customers. Dropping real business
outcomes for being inconvenient would bias the models against precisely the
customers the agency most wants to find. The cost is modest — they widen the
spread by 1.10x, not an order of magnitude.

**Carry forward:** report MAE alongside RMSE in Packages 2 and 6. Squared error
lets five rows dominate a 3,500-row score.

## Package 1 — what the data says

### Budget tier is the strongest signal in the dataset

| tier | campaigns | avg budget | close rate | avg CAC | avg LTV | avg profit | upsell | referral | **profit per ₪ spent** |
|------|-----------|-----------|------------|---------|---------|------------|--------|----------|------------------------|
| Low  | 780       | 1,082     | 4.7%       | 766     | 7.9 mo  | 2,291      | 15.6%  | 7.7%     | **2.10x** |
| Mid  | 1,717     | 3,210     | 8.3%       | 1,024   | 33.6 mo | 21,792     | 66.3%  | 64.2%    | **6.73x** |
| High | 1,003     | 9,908     | 5.4%       | 2,430   | 13.2 mo | 5,186      | 20.5%  | 19.1%    | **0.52x** |

The relationship is not monotonic, and that is the headline. Mid-budget
campaigns outperform on every measure at once — close rate, LTV, profit, upsell
and referral — while High burns 2,430 per acquisition to buy customers who
leave after 13 months.

At 0.52x, high-budget campaigns return less profit than they consume in ad
spend. Spending more is not merely inefficient here; past roughly 5,000 it
appears actively harmful. This is the central input to Package 6.

### The funnel loses most of its volume before the first follow-up

| stage       | count   | of leads | kept from previous |
|-------------|---------|----------|--------------------|
| leads       | 161,772 | 100.0%   | —                  |
| answered    | 97,925  | 60.5%    | 60.5%              |
| follow-up 1 | 76,635  | 47.4%    | 78.3%              |
| follow-up 2 | 56,960  | 35.2%    | 74.3%              |
| follow-up 3 | 46,357  | 28.7%    | 81.4%              |
| follow-up 4 | 41,549  | 25.7%    | 89.6%              |
| follow-up 5 | 29,405  | 18.2%    | 70.8%              |
| closed      | 10,558  | 6.5%     | 35.9%              |

The largest single drop is the first one: 39.5% of leads are never reached at
all. No amount of follow-up discipline recovers a lead that never answered.

### The follow-up paradox is real, and steeper than expected

Grouped by how many calls it took to close:

| calls to close | campaigns | avg LTV | avg profit | upsell rate |
|----------------|-----------|---------|------------|-------------|
| 1              | 91        | 36.4    | 23,199     | 70.3%       |
| 2              | 1,034     | 36.4    | 23,661     | 65.0%       |
| 3              | 598       | 28.2    | 18,076     | 67.1%       |
| 4              | 356       | 17.8    | 7,571      | 35.1%       |
| 5              | 690       | 12.0    | 4,760      | 23.0%       |
| 6              | 395       | 6.7     | 1,934      | 9.4%        |
| 7              | 133       | 7.0     | 1,848      | 5.3%        |
| 8+             | 21        | 6.6     | 1,677      | 4.8%        |

A deal closed in one or two calls is worth roughly **twelve times** one that
takes six, and is seven times as likely to upsell. The cliff sits between calls
3 and 4, where profit falls from 18,076 to 7,571.

**This is correlation, not causation, and the distinction decides the
recommendation.** The plausible reading is that call count is a symptom of lead
quality rather than a cause of value — customers who need convincing six times
were never going to be good customers. If that holds, the lever is
qualification, not call discipline, and instructing reps to stop calling would
destroy the 4,760 average that calls 5+ still produce.

Package 5 has to separate these two readings before recommending anything.

## Package 2 — predicting LTV in months

Three libraries, one set of 5-fold splits shared between them so the comparison
is between models rather than between lucky partitions. 3,496 rows (4 dropped
for a missing target), 16 features, none of them one of the four outcomes.

| model | MAE (months) | RMSE | R² | MAE spread across folds |
|-------|--------------|------|-----|------------------------|
| Baseline (predict the mean) | 11.43 | 12.45 | -0.00 | 0.23 |
| XGBoost  | 2.29 | 3.08 | 0.939 | 0.043 |
| LightGBM | 2.23 | 2.98 | 0.943 | 0.041 |
| **CatBoost** | **2.18** | **2.90** | **0.945** | 0.043 |

**CatBoost wins, but the three are within 0.12 months of each other** — a
practical tie. The interesting gap is against the baseline: predicting the mean
is off by 11.4 months on a target averaging 22, so the models cut the error by
81%.

The fold-to-fold spread is 0.04 months. The score is a property of the model,
not of one convenient split.

### What the model actually uses

| feature | share |
|---------|-------|
| calls_to_closed | 55.9% |
| budget_tier | 11.2% |
| followup_5 | 6.2% |
| customer_acquisition_cost | 4.9% |
| followup_3 | 4.9% |
| ad_budget | 4.3% |

`calls_to_closed` alone carries more than half the model. That is the Package 1
follow-up finding restated by an independent method: how hard a deal was to
close predicts how long the customer stays, and it does so more strongly than
anything about the budget. The same causal caveat applies — this says call
count *predicts* LTV, not that fewer calls *produce* longer retention.

### `purchased` dropped, on evidence

`purchased` is an outcome of the funnel too, though the locked rule names only
the other four. Rather than argue the letter of the list, both feature sets were
cross-validated:

| model | with `purchased` | without | cost of dropping |
|-------|------------------|---------|------------------|
| XGBoost  | 2.292 | 2.287 | −0.005 |
| LightGBM | 2.229 | 2.227 | −0.002 |
| CatBoost | 2.177 | 2.176 | −0.001 |

Every model is unchanged to within a hundredth of a month — all three are
fractionally *better* without it. Removing an outcome-derived feature at zero
cost is strictly better than defending it, so the final model excludes it.

**Shipped:** `models/ltv_months.cbm`, CatBoost trained on all 3,496 usable rows.
Expected error in production is about 2.2 months either way.

## Package 3 — predicting upsell

Three classifiers, stratified 5-fold so every fold holds the same class mix.
3,500 rows, all usable — the target has no missing values.

| model | accuracy | precision | recall | F1 | ROC-AUC | **PR-AUC** |
|-------|----------|-----------|--------|-----|---------|------------|
| Baseline (always "no") | 0.581 | 0.000 | 0.000 | 0.000 | 0.500 | 0.419 |
| XGBoost  | 0.756 | 0.686 | 0.769 | 0.725 | 0.804 | 0.664 |
| LightGBM | 0.758 | 0.684 | 0.785 | 0.731 | 0.804 | 0.661 |
| **CatBoost** | **0.774** | 0.696 | **0.818** | **0.752** | **0.817** | **0.680** |

CatBoost again, and by a wider margin than in Package 2. Recall of 0.818 means
it finds about four in five of the customers who go on to upsell.

Note the baseline row: always answering "no upsell" scores 0.581 accuracy while
being useless, which is why accuracy is reported but not ranked on.

### The imbalance decision — measured, not assumed

The classes split **1.39:1**. "Imbalanced" normally means 4:1 or worse, and the
techniques built for it assume a minority rare enough that a model can ignore it
and still score well. That is not this dataset. Both settings were run anyway:

| model | F1 as-is | F1 weighted | PR-AUC as-is | PR-AUC weighted |
|-------|----------|-------------|--------------|-----------------|
| XGBoost  | 0.7252 | 0.7405 | 0.6641 | 0.6656 |
| LightGBM | 0.7309 | 0.7471 | 0.6610 | 0.6602 |
| CatBoost | 0.7518 | 0.7606 | 0.6796 | 0.6792 |

**The two metrics disagree, and the disagreement is the answer.** Weighting
lifts F1 by 0.01–0.02, which looks like a win. PR-AUC does not move — CatBoost
goes 0.6796 → 0.6792.

PR-AUC is threshold-free and measures *ranking*; F1 is measured at a fixed 0.5
cutoff. So weighting is not making the model better at telling upsellers apart.
It is sliding the cutoff toward recall and collecting the F1 that follows — a
move available directly by choosing a threshold, where the trade is at least
visible.

**Decision: no resampling, no class weights.** Weighting pushes predicted
probabilities away from the true rate, so 0.7 stops meaning "about 70% of these
convert". Package 4 builds its 0–100 score on exactly those probabilities.
Spending calibration we still need to buy a threshold shift is a bad trade.

### `purchased` kept here — the opposite call to Package 2

| | upsell False | upsell True |
|---|---|---|
| **purchased False** | 337 | **0** |
| **purchased True** | 1,697 | 1,466 |

Zero upsells exist without a purchase. `purchased` is not so much a predictor of
upsell as a precondition for it, and "who will upsell" is only ever asked about
someone who bought.

Unlike Package 2, dropping it costs real accuracy:

| model | PR-AUC with | without | cost of dropping |
|-------|-------------|---------|------------------|
| XGBoost  | 0.6641 | 0.6390 | −0.0251 |
| LightGBM | 0.6610 | 0.6402 | −0.0208 |
| CatBoost | 0.6796 | 0.6613 | −0.0183 |

The same column was worth nothing for LTV and is worth ~0.02 PR-AUC here. Kept.

### What the model uses

| feature | share |
|---------|-------|
| purchased | 33.4% |
| calls_to_closed | 18.4% |
| customer_acquisition_cost | 7.7% |
| num_leads | 5.1% |

`calls_to_closed` is second again, after being first in Package 2. Three
independent analyses have now surfaced it.

**Shipped:** `models/upsell.cbm`, CatBoost, no resampling, trained on all 3,500 rows.

## Package 4 — the super-customer score

Predicts `referred` (38.7% positive, 1.58:1) and converts the probability into a
0–100 ranking. CatBoost with tuned hyperparameters and `budget_tier` as a real
category, all three as the brief specifies.

### budget_tier as a category

| encoding | PR-AUC |
|----------|--------|
| real category | 0.6685 |
| integer 0/1/2 | 0.6595 |

+0.0089 for the categorical form. The margin is small and expected to be:
`budget_tier` is a deterministic function of `ad_budget`, which is already a
feature, so a tree can recover the tier either way. It is used because Low/Mid/
High is a label rather than a quantity, and encoding it as 0/1/2 invites the
model to treat the gap Low→Mid as equal to Mid→High, which is meaningless.

### Hyperparameter tuning — honest result

18 combinations over depth, learning rate and L2, each scored by 5-fold PR-AUC.

| depth | lr | l2 | PR-AUC | ROC-AUC |
|-------|-----|-----|--------|---------|
| **6** | **0.03** | **1.0** | **0.6699** | 0.8179 |
| 4 | 0.03 | 5.0 | 0.6646 | 0.8200 |
| 6 | 0.06 | 5.0 | 0.6645 | 0.8135 |

Best is depth 6, lr 0.03, l2 1.0 — but the untuned starting point already scored
0.6685, so **tuning bought +0.0014**. The spread across all 18 combinations is
only 0.0225.

Worth stating plainly rather than presenting the search as a win: on this
dataset CatBoost is close to its best out of the box, and the tuning result is
mainly evidence that the earlier numbers were not a lucky parameter draw.

### Is the probability honest?

Package 3 refused class weighting specifically to protect calibration here, so
the promise gets checked:

| predicted band | customers | avg predicted | actual rate | gap |
|----------------|-----------|---------------|-------------|-----|
| 0.0 – 0.2 | 1,473 | 0.074 | 0.094 | +0.020 |
| 0.2 – 0.4 | 304 | 0.296 | 0.296 | 0.000 |
| 0.4 – 0.6 | 332 | 0.515 | 0.524 | +0.010 |
| 0.6 – 0.8 | 1,278 | 0.691 | 0.678 | −0.014 |
| 0.8 – 1.0 | 113 | 0.838 | 0.761 | **−0.077** |

Brier score 0.1628 (0 perfect, 0.25 a coin flip). Four of five bands agree with
reality to within 0.02 — the probabilities mean what they say, and the Package 3
decision paid off.

**The exception is the top band**, where the model claims 0.838 and delivers
0.761. It is overconfident about exactly the customers it is most confident
about, on only 113 people. Anyone acting on scores above 80 should treat them as
"very likely" rather than as the stated number.

### The 0–100 score

| band | customers | referred | actual rate | lift vs average |
|------|-----------|----------|-------------|-----------------|
| 0–19 | 1,461 | 136 | 9.3% | 0.24x |
| 20–39 | 307 | 90 | 29.3% | 0.76x |
| 40–59 | 326 | 165 | 50.6% | 1.31x |
| 60–79 | 1,275 | 867 | 68.0% | 1.76x |
| 80–100 | 131 | 96 | 73.3% | 1.89x |

The rate climbs at every step, which is the property that makes the score usable:
a team working it top-down meets better prospects the whole way, with no band
that rewards skipping.

**Calibrate expectations on the size of the effect.** Top band refers at 1.9x the
average, not 10x. The score's real value is at the bottom: the 0–19 band holds
1,461 customers — 42% of the book — who refer at 9.3%. Knowing who *not* to
chase is the larger win here.

### Top drivers

| feature | share |
|---------|-------|
| purchased | 25.1% |
| calls_to_closed | 18.7% |
| num_leads | 6.5% |

`calls_to_closed` places in the top two for the third model running.

**Shipped:** `models/super_customer.cbm`, CatBoost depth 6 / lr 0.03 / l2 1.0.

## Package 5 — the follow-up paradox

Four packages put `calls_to_closed` at or near the top. This one exists to
decide whether calling less would *cause* better outcomes or whether call count
is a symptom of lead quality, because the two readings give a sales team
opposite instructions.

**This analysis was built expecting to confirm the "symptom" reading. It did
not, and the finding is reported as it came out.**

### The test

Answer rate — the share of leads who pick up — is a property of the leads,
fixed before anyone chooses a follow-up policy. If call count is really a proxy
for quality, the profit gap should collapse once campaigns are compared only
against others with similar answer rates.

Average profit by lead quality and calls needed:

| lead quality | 1–2 calls | 3–4 calls | 5+ calls |
|--------------|-----------|-----------|----------|
| worst 25% | 22,980 | 11,425 | 3,155 |
| low-mid | 23,357 | 12,126 | 3,605 |
| high-mid | 23,901 | 14,689 | 3,899 |
| best 25% | 23,706 | 17,043 | 4,088 |

| comparison | gap |
|------------|-----|
| ignoring lead quality | 6.8x |
| within matched quality bands | 6.4x |
| **explained by lead quality** | **5%** |

The gap barely moves. It is visible inside *every* quality band — even among the
best-answering 25% of leads, fast deals return 23,706 against 4,088 for slow
ones. The confounding explanation does not survive its own test.

Confounding is nonetheless real, just smaller than the effect. Answer rate does
predict both:

| lead quality | avg answer rate | avg calls to close | avg profit |
|--------------|-----------------|--------------------|------------|
| worst 25% | 0.50 | 4.79 | 7,724 |
| low-mid | 0.58 | 4.12 | 10,566 |
| high-mid | 0.64 | 3.33 | 15,277 |
| best 25% | 0.70 | 2.58 | 19,937 |

### What this licenses, and what it does not

**It does not prove that the sixth call destroys value.** Answer rate is one
proxy for quality, not all of it. Deal size, product fit and rep skill are
unobserved, and any of them could drive both call count and profit.

**It does mean call count survives the most obvious confounder**, which makes it
usable as a signal even with the mechanism unresolved.

### Where the effort actually goes

| | calls |
|---|---|
| into deals that closed | 37,260 |
| into deals that never closed | 74,952 |
| **share of all effort wasted** | **66.8%** |

By tier: Low 57.5%, Mid 69.8%, High 66.3%.

### Recommendations, ordered by how well the data supports them

1. **Fix the top of the funnel.** 63,847 leads — 39% — never answer at all. That
   one stage loses more people than every follow-up stage combined, and no
   calling policy reaches them. This needs no causal claim at all.

2. **Treat call four as a review point, not a cut-off.** Expected profit falls
   from 18,076 at three calls to 7,570 at four. Review rather than stop: 66.8%
   of calling effort already goes into deals that never close, so the waste is
   real, but a blind cap also discards the 4,760 that five-call deals still
   return.

3. **Run the experiment before making it policy.** Randomly cap follow-up at
   four calls for one group, leave another uncapped. That is the only design
   that separates the two readings, and it costs one quarter of data rather
   than a year of a wrong policy.

## Open questions for later packages

- Does the Mid-tier advantage survive controlling for lead volume, or is
  `ad_budget` standing in for something else?
- Is the calls-to-close effect causal or a quality proxy? Package 5.
- Should the profit target be modelled on a log scale given the five outliers?

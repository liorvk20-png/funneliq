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

## Open questions for later packages

- Does the Mid-tier advantage survive controlling for lead volume, or is
  `ad_budget` standing in for something else?
- Is the calls-to-close effect causal or a quality proxy? Package 5.
- Should the profit target be modelled on a log scale given the five outliers?

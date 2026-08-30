"""
Package 6, second half — a model for cumulative_profit.

The simulator in package6_budget.py deliberately does not use a model: at the
moment a budget is set, none of the funnel columns exist yet, so a model that
reads them answers a question no planner can ask.

This file builds that model anyway, for the other job. Once a campaign has run,
"what drove the profit" is a real question, and a model answers it far better
than a table of averages can. Keeping the two apart — and saying which is which
— is the point.

Run from the repo root:  python analysis/package6b_profit_model.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.model_selection import KFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_raw, target_frame

warnings.filterwarnings("ignore")
SEED = 42
TARGET = "cumulative_profit"


def models():
    from catboost import CatBoostRegressor
    from lightgbm import LGBMRegressor
    from xgboost import XGBRegressor

    return {
        "Baseline (mean)": DummyRegressor(strategy="mean"),
        "XGBoost": XGBRegressor(
            n_estimators=600, learning_rate=0.05, max_depth=5, subsample=0.8,
            colsample_bytree=0.8, random_state=SEED, n_jobs=-1),
        "LightGBM": LGBMRegressor(
            n_estimators=600, learning_rate=0.05, max_depth=5, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=-1, verbose=-1),
        "CatBoost": CatBoostRegressor(
            iterations=600, learning_rate=0.05, depth=5, random_seed=SEED,
            verbose=0, allow_writing_files=False),
    }


def score(y, pred) -> dict:
    err = y - pred
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "R2": 1 - float(np.sum(err ** 2)) / ss_tot,
    }


def cross_validate(X, y, folds=5) -> pd.DataFrame:
    kf = KFold(n_splits=folds, shuffle=True, random_state=SEED)
    rows = []
    for name, model in models().items():
        per_fold = []
        for tr, te in kf.split(X):
            m = model.__class__(**model.get_params())
            m.fit(X.iloc[tr], y.iloc[tr])
            per_fold.append(score(y.iloc[te].to_numpy(), m.predict(X.iloc[te])))
        agg = {k: np.mean([f[k] for f in per_fold]) for k in per_fold[0]}
        agg["MAE_sd"] = np.std([f["MAE"] for f in per_fold])
        rows.append({"model": name, **agg})
    return pd.DataFrame(rows).set_index("model")


def main() -> None:
    df = load_raw()
    X, y = target_frame(df, TARGET)

    print(f"target: {TARGET}   rows: {len(X):,} of {len(df):,} "
          f"({len(df) - len(X)} dropped for a missing target)")
    print(f"y: mean {y.mean():,.0f}  median {y.median():,.0f}  "
          f"sd {y.std():,.0f}  max {y.max():,.0f}\n")

    print("=" * 76)
    print("5-FOLD CROSS-VALIDATION")
    print("=" * 76)
    res = cross_validate(X, y)
    print(res.round(1).to_string())

    best = res.drop(index="Baseline (mean)")["MAE"].idxmin()
    base = res.loc["Baseline (mean)", "MAE"]
    print(f"\nBest: {best} — MAE {res.loc[best, 'MAE']:,.0f} against {base:,.0f} "
          f"for predicting the mean ({(1 - res.loc[best, 'MAE'] / base) * 100:.0f}% better).")
    print("\nMAE leads and RMSE follows, per the Package 1 decision to keep the five")
    print("extreme profits. The gap between them here is the clearest sign of what")
    print("those rows cost: squared error is dominated by a handful of campaigns.")

    print("\n" + "=" * 76)
    print(f"WHAT DRIVES PROFIT, ACCORDING TO {best.upper()}")
    print("=" * 76)
    m = models()[best]
    m = m.__class__(**m.get_params())
    m.fit(X, y)
    imp = (pd.Series(m.feature_importances_, index=X.columns)
           .sort_values(ascending=False) / m.feature_importances_.sum() * 100)
    for f, v in imp.head(8).items():
        print(f"  {f:<28}{v:>6.1f}%  {'#' * int(v / 2)}")

    print("\n" + "=" * 76)
    print("WHY THIS MODEL CANNOT PLAN A BUDGET")
    print("=" * 76)
    known_in_advance = ["ad_budget", "budget_tier"]
    after_the_fact = [c for c in X.columns if c not in known_in_advance]
    share = imp[after_the_fact].sum()
    print("Split the model's own importances by when each input becomes known:\n")
    print(f"  known when the budget is set : {imp[known_in_advance].sum():>5.1f}%  "
          f"({', '.join(known_in_advance)})")
    print(f"  only known after it has run  : {share:>5.1f}%  ({len(after_the_fact)} columns)")
    print(f"""
{share:.0f}% of what this model relies on does not exist at the moment a budget is
chosen. Handed only the two columns a planner actually has, it would be a far
weaker model than its headline score suggests — and that headline is exactly
what makes a profit model tempting to misuse for planning.

So the two halves of Package 6 answer different questions:

  package6_budget.py       plans   — observed returns per budget level, using
                                     only the decision variable
  package6b_profit_model.py explains — what moved profit, once a campaign ran

Reporting one number from the wrong half is the specific mistake this split
exists to prevent.
""")

    print("=" * 76)
    print("FINAL MODEL")
    print("=" * 76)
    final = models()[best]
    final = final.__class__(**final.get_params())
    final.fit(X, y)
    out = Path(__file__).resolve().parent.parent / "models"
    out.mkdir(exist_ok=True)
    path = out / "profit.cbm"
    final.save_model(str(path))
    print(f"trained on all {len(X):,} usable rows")
    print(f"saved: models/{path.name}  ({path.stat().st_size / 1024:.0f} KB)")
    print(f"expected error: about {res.loc[best, 'MAE']:,.0f} either way")
    print("Use: explanation and attribution. Not budget planning.")


if __name__ == "__main__":
    main()

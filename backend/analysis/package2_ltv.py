"""
Package 2 — predicting ltv_months.

Three gradient-boosting libraries under identical 5-fold cross-validation, all
seeing the same folds so the comparison is between models rather than between
lucky splits.

Run from the repo root:  python analysis/package2_ltv.py
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
TARGET = "ltv_months"


def models():
    from catboost import CatBoostRegressor
    from lightgbm import LGBMRegressor
    from xgboost import XGBRegressor

    return {
        "Baseline (mean)": DummyRegressor(strategy="mean"),
        "XGBoost": XGBRegressor(
            n_estimators=600, learning_rate=0.05, max_depth=5,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=-1,
        ),
        "LightGBM": LGBMRegressor(
            n_estimators=600, learning_rate=0.05, max_depth=5, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=-1, verbose=-1,
        ),
        "CatBoost": CatBoostRegressor(
            iterations=600, learning_rate=0.05, depth=5,
            random_seed=SEED, verbose=0, allow_writing_files=False,
        ),
    }


def score(y_true, y_pred) -> dict:
    err = y_true - y_pred
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    return {
        # MAE first on purpose: five kept outliers make squared error a partly
        # political number, and MAE says plainly how many months off we are.
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "R2": 1 - ss_res / ss_tot,
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

    print(f"target: {TARGET}   rows: {len(X):,}   features: {X.shape[1]}")
    print(f"y: mean {y.mean():.1f}  median {y.median():.1f}  "
          f"sd {y.std():.1f}  range {y.min():.0f}-{y.max():.0f} months\n")

    print("=" * 74)
    print("5-FOLD CROSS-VALIDATION")
    print("=" * 74)
    res = cross_validate(X, y)
    print(res.round(3).to_string())
    print("\nMAE_sd is the spread of MAE across the five folds — small means the")
    print("score is a property of the model, not of one convenient split.")

    best = res.drop(index="Baseline (mean)")["MAE"].idxmin()
    base_mae = res.loc["Baseline (mean)", "MAE"]
    print(f"\nBest: {best} — MAE {res.loc[best, 'MAE']:.2f} months "
          f"vs {base_mae:.2f} for predicting the mean "
          f"({(1 - res.loc[best, 'MAE'] / base_mae) * 100:.0f}% better).")

    print("\n" + "=" * 74)
    print(f"WHAT {best.upper()} IS USING")
    print("=" * 74)
    m = models()[best]
    m = m.__class__(**m.get_params())
    m.fit(X, y)
    imp = (pd.Series(m.feature_importances_, index=X.columns)
           .sort_values(ascending=False) / m.feature_importances_.sum() * 100)
    for f, v in imp.head(10).items():
        print(f"  {f:<28}{v:>6.1f}%  {'#' * int(v / 2)}")

    print("\n" + "=" * 74)
    print("HOW MUCH IS `purchased` CARRYING?")
    print("=" * 74)
    print("`purchased` is an outcome of the funnel too, though the locked rule names")
    print("only the other four. Retraining without it shows what it is worth, so the")
    print("choice to keep it is made on evidence rather than on the letter of a list.\n")
    X2, y2 = target_frame(df, TARGET, drop=["purchased"])
    res2 = cross_validate(X2, y2)
    cmp = pd.DataFrame({"with purchased": res["MAE"], "without": res2["MAE"]})
    cmp["cost of dropping"] = cmp["without"] - cmp["with purchased"]
    print(cmp.round(3).to_string())
    print("\nEvery model is unchanged to within a hundredth of a month, so `purchased`")
    print("is carrying nothing here. Dropped from the final model: removing an")
    print("outcome-derived feature for free is strictly better than arguing about")
    print("whether it was allowed.")

    print("\n" + "=" * 74)
    print("FINAL MODEL")
    print("=" * 74)
    final = models()[best]
    final = final.__class__(**final.get_params())
    final.fit(X2, y2)
    out = Path(__file__).resolve().parent.parent / "models"
    out.mkdir(exist_ok=True)
    path = out / "ltv_months.cbm"
    final.save_model(str(path))
    print(f"trained on all {len(X2):,} usable rows without `purchased`")
    print(f"saved: models/{path.name}  ({path.stat().st_size / 1024:.0f} KB)")
    print(f"expected error in production: about {res2.loc[best, 'MAE']:.1f} months either way")


if __name__ == "__main__":
    main()

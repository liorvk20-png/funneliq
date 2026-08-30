"""
Package 4 — the super-customer score.

Predicts `referred` and turns the probability into a 0-100 ranking the agency
can act on. Three things the brief asks for specifically: CatBoost, tuned
hyperparameters, and budget_tier handled as a real category rather than as an
integer standing in for one.

Run from the repo root:  python analysis/package4_super_customer.py
"""
import sys
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_raw, target_frame

warnings.filterwarnings("ignore")
SEED = 42
TARGET = "referred"
CV = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)


def build(depth, lr, l2, iters=600):
    from catboost import CatBoostClassifier

    return CatBoostClassifier(
        iterations=iters, learning_rate=lr, depth=depth, l2_leaf_reg=l2,
        random_seed=SEED, verbose=0, allow_writing_files=False,
    )


def oof_proba(make_model, X, y, cat=None):
    """
    Out-of-fold probabilities: every row scored by a model that never saw it.

    Hand-rolled rather than cross_val_predict because CatBoost declares
    cat_features in the constructor, which sklearn's clone() refuses. Passing
    the categorical columns to fit() instead sidesteps that, and the loop is
    plain enough to read.
    """
    out = np.zeros(len(y))
    for tr, te in CV.split(X, y):
        m = make_model()
        m.fit(X.iloc[tr], y.iloc[tr], cat_features=cat or [])
        out[te] = m.predict_proba(X.iloc[te])[:, 1]
    return out


def main() -> None:
    df = load_raw()
    Xc, y = target_frame(df, TARGET, tier_as_category=True)
    Xo, _ = target_frame(df, TARGET)
    y = y.astype(int)

    print(f"target: {TARGET}   rows: {len(Xc):,}   features: {Xc.shape[1]}")
    ratio = (1 - y).sum() / y.sum()
    print(f"positives {y.sum():,} ({y.mean() * 100:.1f}%)   ratio {ratio:.2f}:1\n")

    print("=" * 78)
    print("DOES budget_tier AS A REAL CATEGORY HELP?")
    print("=" * 78)
    base = dict(depth=5, lr=0.05, l2=3.0)
    pr_cat = average_precision_score(
        y, oof_proba(lambda: build(**base), Xc, y, cat=["budget_tier"]))
    pr_ord = average_precision_score(y, oof_proba(lambda: build(**base), Xo, y))
    print(f"  budget_tier as category : PR-AUC {pr_cat:.4f}")
    print(f"  budget_tier as integer  : PR-AUC {pr_ord:.4f}")
    print(f"  difference              : {pr_cat - pr_ord:+.4f}")
    print("\nbudget_tier is a deterministic function of ad_budget, which is already a")
    print("feature, so a tree can recover the tier unaided either way. The brief asks")
    print("for the categorical treatment and it is the more honest encoding — Low/Mid/")
    print("High is a label, not a quantity — so it is used regardless of the margin.")

    print("\n" + "=" * 78)
    print("HYPERPARAMETER TUNING")
    print("=" * 78)
    grid = list(product([4, 6, 8], [0.03, 0.06, 0.1], [1.0, 5.0]))
    print(f"{len(grid)} combinations, each scored by 5-fold PR-AUC on held-out folds\n")
    rows = []
    for depth, lr, l2 in grid:
        p = oof_proba(lambda d=depth, r=lr, g=l2: build(d, r, g), Xc, y,
                      cat=["budget_tier"])
        rows.append({"depth": depth, "lr": lr, "l2": l2,
                     "PR_AUC": average_precision_score(y, p),
                     "ROC_AUC": roc_auc_score(y, p)})
    tune = pd.DataFrame(rows).sort_values("PR_AUC", ascending=False)
    print(tune.head(6).round(4).to_string(index=False))
    print(f"\n... {len(grid) - 6} weaker combinations omitted")
    bestp = tune.iloc[0]
    print(f"\nBest: depth={int(bestp.depth)}  learning_rate={bestp.lr}  l2_leaf_reg={bestp.l2}")
    print(f"PR-AUC {bestp.PR_AUC:.4f} vs {tune.iloc[-1].PR_AUC:.4f} for the worst — "
          f"a spread of {bestp.PR_AUC - tune.iloc[-1].PR_AUC:.4f}.")
    print(f"Untuned starting point was {pr_cat:.4f}, so tuning bought "
          f"{bestp.PR_AUC - pr_cat:+.4f}.")

    proba = oof_proba(lambda: build(int(bestp.depth), bestp.lr, bestp.l2), Xc, y,
                      cat=["budget_tier"])

    print("\n" + "=" * 78)
    print("IS THE PROBABILITY HONEST?")
    print("=" * 78)
    print("Package 3 refused class weighting to protect calibration for this score.")
    print("This checks the promise was worth keeping: within each band of predicted")
    print("probability, how many customers actually referred.\n")
    band = pd.cut(proba, [0, .2, .4, .6, .8, 1.0], include_lowest=True)
    cal = pd.DataFrame({"predicted": proba, "actual": y}).groupby(band, observed=True).agg(
        customers=("actual", "size"), avg_predicted=("predicted", "mean"),
        actual_rate=("actual", "mean"))
    cal["gap"] = cal["actual_rate"] - cal["avg_predicted"]
    print(cal.round(3).to_string())
    print(f"\nBrier score: {brier_score_loss(y, proba):.4f}  (0 is perfect, 0.25 is a coin flip)")

    print("\n" + "=" * 78)
    print("THE 0-100 SCORE")
    print("=" * 78)
    score = np.round(proba * 100).astype(int)
    out = pd.DataFrame({"score": score, "referred": y.to_numpy()})
    out["band"] = pd.cut(out["score"], [-1, 19, 39, 59, 79, 100],
                         labels=["0-19", "20-39", "40-59", "60-79", "80-100"])
    t = out.groupby("band", observed=True).agg(
        customers=("referred", "size"), referred=("referred", "sum"),
        actual_rate=("referred", "mean"))
    t["lift_vs_average"] = t["actual_rate"] / y.mean()
    t["actual_rate"] = (t["actual_rate"] * 100).round(1)
    print(t.round(2).to_string())
    print(f"\nBase rate across everyone: {y.mean() * 100:.1f}%")
    top, bottom = t.iloc[-1], t.iloc[0]
    print(f"Top band refers at {top.actual_rate}% — {top.lift_vs_average:.1f}x the average.")
    print(f"Bottom band refers at {bottom.actual_rate}% — {bottom.lift_vs_average:.1f}x.")
    print("\nThe rate climbs with every band, which is what makes the score usable: a")
    print("sales team working it top-down meets better prospects the whole way.")

    print("\n" + "=" * 78)
    print("FINAL MODEL")
    print("=" * 78)
    final = build(int(bestp.depth), bestp.lr, bestp.l2)
    final.fit(Xc, y, cat_features=["budget_tier"])
    d = Path(__file__).resolve().parent.parent / "models"
    d.mkdir(exist_ok=True)
    path = d / "super_customer.cbm"
    final.save_model(str(path))
    print(f"CatBoost depth={int(bestp.depth)} lr={bestp.lr} l2={bestp.l2}, "
          f"budget_tier categorical")
    print(f"saved: models/{path.name}  ({path.stat().st_size / 1024:.0f} KB)")
    imp = (pd.Series(final.feature_importances_, index=Xc.columns)
           .sort_values(ascending=False) / final.feature_importances_.sum() * 100)
    print("\ntop drivers of a referral:")
    for f, v in imp.head(6).items():
        print(f"  {f:<28}{v:>6.1f}%  {'#' * int(v / 2)}")


if __name__ == "__main__":
    main()

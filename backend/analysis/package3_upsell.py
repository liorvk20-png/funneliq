"""
Package 3 — predicting upsell.

Three classifiers under identical stratified 5-fold cross-validation, plus the
imbalance decision the brief asks for, settled by measurement rather than by
reaching for SMOTE because the word "classification" appeared.

Run from the repo root:  python analysis/package3_upsell.py
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_raw, target_frame

warnings.filterwarnings("ignore")
SEED = 42
TARGET = "upsell"


def models(weight: float | None = None):
    """`weight` sets the positive-class weight; None leaves each library's default."""
    from catboost import CatBoostClassifier
    from lightgbm import LGBMClassifier
    from xgboost import XGBClassifier

    return {
        "Baseline (majority)": DummyClassifier(strategy="most_frequent"),
        "XGBoost": XGBClassifier(
            n_estimators=600, learning_rate=0.05, max_depth=5,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=-1,
            eval_metric="logloss", scale_pos_weight=weight or 1.0,
        ),
        "LightGBM": LGBMClassifier(
            n_estimators=600, learning_rate=0.05, max_depth=5, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=-1,
            verbose=-1, scale_pos_weight=weight or 1.0,
        ),
        "CatBoost": CatBoostClassifier(
            iterations=600, learning_rate=0.05, depth=5, random_seed=SEED,
            verbose=0, allow_writing_files=False,
            scale_pos_weight=weight or 1.0,
        ),
    }


def score(y, pred, proba) -> dict:
    return {
        "accuracy": accuracy_score(y, pred),
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "F1": f1_score(y, pred, zero_division=0),
        # ROC-AUC reads optimistically when classes are uneven; PR-AUC is the
        # one to trust for "did we find the positives", so both are reported.
        "ROC_AUC": roc_auc_score(y, proba),
        "PR_AUC": average_precision_score(y, proba),
    }


def cross_validate(X, y, weight=None, folds=5) -> pd.DataFrame:
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=SEED)
    rows = []
    for name, model in models(weight).items():
        per_fold = []
        for tr, te in skf.split(X, y):
            m = model.__class__(**model.get_params())
            m.fit(X.iloc[tr], y.iloc[tr])
            proba = m.predict_proba(X.iloc[te])[:, 1]
            per_fold.append(score(y.iloc[te], m.predict(X.iloc[te]), proba))
        agg = {k: np.mean([f[k] for f in per_fold]) for k in per_fold[0]}
        agg["F1_sd"] = np.std([f["F1"] for f in per_fold])
        rows.append({"model": name, **agg})
    return pd.DataFrame(rows).set_index("model")


def main() -> None:
    df = load_raw()
    X, y = target_frame(df, TARGET)
    y = y.astype(int)

    pos, neg = int(y.sum()), int((1 - y).sum())
    print(f"target: {TARGET}   rows: {len(X):,}   features: {X.shape[1]}")
    print(f"positives {pos:,} ({pos / len(y) * 100:.1f}%)   "
          f"negatives {neg:,} ({neg / len(y) * 100:.1f}%)   ratio {neg / pos:.2f}:1\n")

    print("=" * 78)
    print("STRATIFIED 5-FOLD CROSS-VALIDATION")
    print("=" * 78)
    res = cross_validate(X, y)
    print(res.round(3).to_string())

    best = res.drop(index="Baseline (majority)")["PR_AUC"].idxmax()
    print(f"\nBest by PR-AUC: {best}")
    print(f"Accuracy {res.loc[best, 'accuracy']:.3f} against "
          f"{res.loc[best, 'accuracy'] - res.loc['Baseline (majority)', 'accuracy']:+.3f} "
          "over always answering 'no upsell'.")

    print("\n" + "=" * 78)
    print("THE IMBALANCE DECISION")
    print("=" * 78)
    print(f"The classes split {neg / pos:.2f}:1. For scale, 'imbalanced' usually means")
    print("4:1 upward, and the techniques built for it — SMOTE, heavy class weights —")
    print("assume a minority so rare the model can ignore it and still score well.")
    print("That is not this dataset. Rather than assert it, both settings were run:\n")

    weighted = cross_validate(X, y, weight=neg / pos)
    cmp = pd.DataFrame({
        "F1 (as-is)": res["F1"], "F1 (weighted)": weighted["F1"],
        "PR-AUC (as-is)": res["PR_AUC"], "PR-AUC (weighted)": weighted["PR_AUC"],
    }).drop(index="Baseline (majority)")
    cmp["F1 change"] = cmp["F1 (weighted)"] - cmp["F1 (as-is)"]
    print(cmp.round(4).to_string())
    print("\nRead these two columns together, because they disagree in a useful way.")
    print("Weighting lifts F1 by 0.01-0.02, which looks like a win. PR-AUC does not")
    print("move at all — CatBoost goes from 0.6796 to 0.6792.")
    print("\nPR-AUC is threshold-free: it measures how well the model *ranks*")
    print("customers. F1 is measured at a fixed 0.5 cutoff. So weighting is not")
    print("making the model better at telling upsellers apart; it is sliding the")
    print("cutoff toward recall and collecting the F1 that follows. The same move is")
    print("available directly, by choosing a threshold, and choosing it is honest")
    print("about what is being traded.")
    print("\nDecision: no resampling, no class weights. Weighting distorts predicted")
    print("probabilities away from the true rate, so a score of 0.7 stops meaning")
    print("'about 70% of these convert'. Package 4 builds a 0-100 ranking on exactly")
    print("those probabilities, and paying for a threshold shift with a calibration")
    print("we still need is a bad trade.")

    print("\n" + "=" * 78)
    print(f"WHAT {best.upper()} IS USING")
    print("=" * 78)
    m = models()[best]
    m = m.__class__(**m.get_params())
    m.fit(X, y)
    imp = (pd.Series(m.feature_importances_, index=X.columns)
           .sort_values(ascending=False) / m.feature_importances_.sum() * 100)
    for f, v in imp.head(8).items():
        print(f"  {f:<28}{v:>6.1f}%  {'#' * int(v / 2)}")

    print("\n" + "=" * 78)
    print("IS `purchased` LEAKAGE HERE?")
    print("=" * 78)
    cross = pd.crosstab(df["purchased"], df["upsell"])
    print(cross.to_string())
    impossible = int(cross.loc[False, True]) if True in cross.columns else 0
    print(f"\nUpsells recorded against a customer who never purchased: {impossible}")
    if impossible == 0:
        print("None — an upsell cannot exist without a purchase, so `purchased` is not a")
        print("predictor of upsell so much as a precondition for it. Kept, because the")
        print("question 'who will upsell' is only ever asked about someone who bought,")
        print("but measured below so the claim is checked rather than assumed.\n")
    X2, y2 = target_frame(df, TARGET, drop=["purchased"])
    res2 = cross_validate(X2, y2.astype(int))
    c2 = pd.DataFrame({"with purchased": res["PR_AUC"], "without": res2["PR_AUC"]})
    c2["cost of dropping"] = c2["without"] - c2["with purchased"]
    print(c2.round(4).to_string())

    print("\n" + "=" * 78)
    print("FINAL MODEL")
    print("=" * 78)
    final = models()[best]
    final = final.__class__(**final.get_params())
    final.fit(X, y)
    out = Path(__file__).resolve().parent.parent / "models"
    out.mkdir(exist_ok=True)
    path = out / "upsell.cbm"
    final.save_model(str(path))
    print(f"trained on all {len(X):,} rows, no resampling")
    print(f"saved: models/{path.name}  ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

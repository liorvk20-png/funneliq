"""
Training a company's models on that company's own data.

Until now every prediction came from models fitted on the reference dataset the
project was built with — nobody's data. The page said so, which is honest and
not the same as useful.

Two things matter more here than accuracy.

The first is the leakage rule, which is locked: upsell, referred,
cumulative_profit and ltv_months are downstream outcomes, and none may ever be
a feature when another is the target. They are removed by construction rather
than by remembering to drop them, so a new target cannot quietly reintroduce
one.

The second is that a small company's model must not be presented as if it were
a large one's. Measured on the reference data, holding out a quarter, a model
fitted on ten rows beat guessing the average by 4% — inside the noise. At
twenty-five rows it was 45% better, at fifty 69%. So every model is scored
against the dumbest possible alternative on the company's own data, that
comparison travels with it, and a model that does not beat guessing is recorded
as not useful and never shown as a prediction.
"""
from __future__ import annotations

import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# The four downstream outcomes. Locked: never a feature, only ever a target.
OUTCOMES = ["upsell", "referred", "cumulative_profit", "ltv_months"]

# target -> whether it is a number to predict or a yes/no to rank
TARGETS = {
    "ltv_months": "regression",
    "cumulative_profit": "regression",
    "upsell": "classification",
    "referred": "classification",
}

# Below this there is nothing worth fitting: the model does not beat guessing,
# and the measurement of whether it does is itself unstable. Descriptive
# figures still work at any size, and that is what the company gets.
MIN_ROWS = 10
# Below this a held-out estimate is too noisy to quote, so the comparison is
# made by leaving out one row at a time instead of by folds.
SMALL = 40


@dataclass
class Trained:
    target: str
    kind: str
    rows: int
    model_bytes: bytes
    # How wrong the model is, and how wrong the laziest possible answer is.
    # Both on this company's data, both meaningless without the other.
    score: float
    baseline: float
    better_by_pct: float
    useful: bool
    note: str


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column in ("purchased", "upsell", "referred"):
        if column in df:
            df[column] = df[column].astype("boolean").astype(float)
    for column in [*OUTCOMES, "ad_budget"]:
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Everything the company reported that is not an outcome.

    budget_tier is dropped rather than encoded: it is a fixed function of
    ad_budget, so a tree recovers the same splits from the number itself, and
    a company's own tier boundaries may not be ours.
    """
    drop = [c for c in [*OUTCOMES, "id", "company_id", "upload_id",
                        "budget_tier", "created_at"] if c in df.columns]
    X = df.drop(columns=drop)
    return X.apply(pd.to_numeric, errors="coerce")


def _fit(kind: str, X, y, iterations: int):
    from catboost import CatBoostClassifier, CatBoostRegressor
    cls = CatBoostRegressor if kind == "regression" else CatBoostClassifier
    model = cls(iterations=iterations, learning_rate=0.06, depth=4, verbose=0,
                allow_writing_files=False, thread_count=2,
                **({"loss_function": "Logloss"} if kind == "classification" else {}))
    model.fit(X, y)
    return model


def _predict(model, kind: str, X):
    if kind == "regression":
        return model.predict(X)
    return model.predict_proba(X)[:, 1]


def _error(kind: str, truth, predicted) -> float:
    """
    Mean absolute error for a number; Brier score for a probability.

    Brier is chosen over accuracy or AUC because it is the same shape of
    quantity as MAE — smaller is better, zero is perfect, and the baseline is
    computed the same way — so one comparison covers both kinds of target.
    """
    truth = np.asarray(truth, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if kind == "regression":
        return float(np.abs(predicted - truth).mean())
    return float(((predicted - truth) ** 2).mean())


def _honest_error(kind: str, X, y, iterations: int) -> tuple[float, float]:
    """
    What the model gets wrong on rows it did not see, and what guessing gets
    wrong on the same rows.

    Scoring on the training rows would report a number close to zero for every
    company regardless of whether the model learned anything, which is the one
    result guaranteed to mislead. Below forty rows each row is held out in turn
    rather than in folds, because a fifth of thirty rows is six rows and an
    estimate from six is worth less than the time it takes to compute.
    """
    n = len(X)
    folds = n if n < SMALL else 5
    step = max(1, n // folds)
    order = np.random.default_rng(0).permutation(n)

    predictions, baselines, truths = [], [], []
    for start in range(0, n, step):
        test = order[start:start + step]
        train = np.setdiff1d(order, test)
        if len(train) < 2 or len(test) == 0:
            continue
        ytr, yte = y.iloc[train], y.iloc[test]
        if kind == "classification" and ytr.nunique() < 2:
            # Every training row has the same answer, so there is nothing to
            # learn from this split; the baseline still covers it.
            continue
        model = _fit(kind, X.iloc[train], ytr, iterations)
        predictions.extend(_predict(model, kind, X.iloc[test]))
        # The laziest defensible answer: the average of what was seen. For a
        # yes/no that is the rate at which it happened.
        baselines.extend([float(ytr.mean())] * len(test))
        truths.extend(yte.tolist())

    if not truths:
        return float("nan"), float("nan")
    return _error(kind, truths, predictions), _error(kind, truths, baselines)


def train(rows: list[dict]) -> list[Trained]:
    """
    Fit what can be fitted from these rows, and say how good each one is.

    A target with too few usable rows, or only one distinct answer, is skipped
    rather than fitted — an upsell model for a company where nobody upsold
    would predict "no" perfectly and mean nothing.
    """
    df = _frame(rows)
    if len(df) < MIN_ROWS:
        return []

    out: list[Trained] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for target, kind in TARGETS.items():
            if target not in df.columns:
                continue
            usable = df[df[target].notna()]
            if len(usable) < MIN_ROWS:
                continue
            X, y = features(usable), usable[target].astype(float)
            if kind == "classification" and y.nunique() < 2:
                continue
            if kind == "regression" and y.nunique() < 2:
                continue

            iterations = 200 if len(X) < 100 else 400
            score, baseline = _honest_error(kind, X, y, iterations)
            if score != score or baseline != baseline:  # NaN: nothing measurable
                continue

            better = 0.0 if baseline == 0 else round(100 * (baseline - score) / baseline, 1)
            model = _fit(kind, X, y, iterations)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "m.cbm"
                model.save_model(str(path))
                blob = path.read_bytes()

            out.append(Trained(
                target=target, kind=kind, rows=len(X), model_bytes=blob,
                score=round(score, 4), baseline=round(baseline, 4),
                better_by_pct=better,
                # The whole point of measuring. A model no better than the
                # average is not a weak prediction to show with a caveat, it is
                # a prediction we do not have.
                useful=better > 0,
                note=_note(len(X), better),
            ))
    return out


def _note(rows: int, better: float) -> str:
    if better <= 0:
        return ("על הנתונים שלך המודל אינו מדויק יותר מניחוש הממוצע, "
                "ולכן איננו מציגים ממנו תחזיות.")
    if rows < 25:
        return (f"מבוסס על {rows} קמפיינים בלבד. זהו אומדן גס — "
                "גם הערכת הדיוק עצמה אינה יציבה בכמות כזו.")
    if rows < 100:
        return f"מבוסס על {rows} קמפיינים. שימושי, וישתפר עם כל חודש שתוסיף."
    return f"מבוסס על {rows} קמפיינים."


def load(model_b64: str, kind: str):
    """A stored model, ready to predict with."""
    import base64

    from catboost import CatBoostClassifier, CatBoostRegressor
    cls = CatBoostRegressor if kind == "regression" else CatBoostClassifier
    model = cls()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.cbm"
        path.write_bytes(base64.b64decode(model_b64))
        model.load_model(str(path))
    return model


def predict_one(model, kind: str, record: dict) -> float:
    """
    Score a single stored campaign.

    The frame is rebuilt through the same features() the model was fitted with,
    and reindexed onto the columns the model actually saw. A company that adds
    a column between one upload and the next would otherwise hand the model a
    different shape than it was trained on, and CatBoost would either refuse or,
    worse, line the values up against the wrong features.
    """
    X = features(_frame([record]))
    X = X.reindex(columns=list(model.feature_names_), fill_value=np.nan)
    return float(_predict(model, kind, X)[0])

"""
Model loading and inference.

All three winning models are CatBoost, so serving needs only that library —
not xgboost, lightgbm or scikit-learn, which stay in requirements-ml.txt where
training uses them.

Models load once at import and stay in memory. Railway runs a long-lived
process, so the cost is paid at startup rather than on every request.
"""
from pathlib import Path

import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor

MODELS = Path(__file__).resolve().parent.parent / "models"

# The tier the campaign's budget falls into. Matches the generated column in
# schema.sql and the encoding used during training, so a value means the same
# thing here as it did when the models learned it.
TIERS = ["Low", "Mid", "High"]


def _tier(budget: float) -> str:
    return "Low" if budget <= 1500 else ("Mid" if budget <= 5000 else "High")


def _load(cls, name: str):
    m = cls()
    m.load_model(str(MODELS / name))
    return m


ltv = _load(CatBoostRegressor, "ltv_months.cbm")
profit = _load(CatBoostRegressor, "profit.cbm")
upsell = _load(CatBoostClassifier, "upsell.cbm")
super_customer = _load(CatBoostClassifier, "super_customer.cbm")


def predict(record: dict) -> dict:
    """
    Score one campaign with all three models.

    Each model gets its own columns in its own order via feature_names_, because
    the three were trained on slightly different sets — `purchased` is in two of
    them and not the third, and budget_tier is a string for the super-customer
    model and an ordinal for the other two. Selecting by the model's own list
    rather than a shared frame is what keeps that from silently misaligning.
    """
    row = dict(record)
    budget = float(row.get("ad_budget") or 0)
    tier = _tier(budget)

    numeric = pd.DataFrame([{**row, "budget_tier": TIERS.index(tier)}])
    categorical = pd.DataFrame([{**row, "budget_tier": tier}])

    months = float(ltv.predict(numeric[ltv.feature_names_])[0])
    money = float(profit.predict(numeric[profit.feature_names_])[0])
    p_upsell = float(upsell.predict_proba(numeric[upsell.feature_names_])[0][1])
    p_referral = float(super_customer.predict_proba(
        categorical[super_customer.feature_names_])[0][1])

    return {
        "ltvMonths": round(months, 1),
        # Reported with the error the model actually had in cross-validation, so
        # a single number never reads as more precise than it is.
        "ltvMarginMonths": 2.2,
        "upsellProbability": round(p_upsell, 3),
        "superCustomerScore": round(p_referral * 100),
        # Above 80 the model was measurably overconfident in Package 4 — it
        # claimed 0.838 and delivered 0.761 — so the flag travels with the score.
        "scoreCaveat": "overconfident above 80" if p_referral * 100 > 80 else None,
        "profit": round(money),
        "profitMargin": 2462,
        # 97.7% of this model's importance sits in columns that only exist after
        # a campaign has run, so it explains an outcome rather than forecasting
        # one. The label travels with the number to stop it being read as a
        # budget forecast, which is the one way it is genuinely misleading.
        "profitUse": "explains a completed campaign, not a budget forecast",
        "budgetTier": tier,
    }

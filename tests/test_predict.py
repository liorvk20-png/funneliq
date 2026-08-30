"""
Inference. The three models were trained on slightly different feature sets, so
each is fed by its own feature_names_ — a shared frame would misalign silently
and still return a number.
"""
import pytest

from app.predict import ltv, predict, profit, super_customer, upsell

CAMPAIGN = {
    "ad_budget": 2500, "num_leads": 36, "leads_answered": 24, "leads_not_answered": 12,
    "followup_1": 19, "followup_2": 14, "followup_3": 11, "followup_4": 10,
    "followup_5": 7, "not_closed": 5, "closed": 2, "calls_to_closed": 2,
    "calls_to_not_closed": 4, "customer_acquisition_cost": 1250, "purchased": True,
}


def test_all_four_models_loaded():
    for model in (ltv, upsell, super_customer, profit):
        assert model.feature_names_


def test_no_model_was_trained_on_an_outcome():
    """The leakage rule, checked on the artifacts rather than the training code."""
    for model in (ltv, upsell, super_customer, profit):
        leaked = set(model.feature_names_) & {
            "upsell", "referred", "cumulative_profit", "ltv_months"}
        assert leaked == set()


def test_prediction_shape():
    p = predict(CAMPAIGN)
    assert set(p) == {"ltvMonths", "ltvMarginMonths", "upsellProbability",
                      "superCustomerScore", "scoreCaveat", "profit",
                      "profitMargin", "profitUse", "budgetTier"}


def test_predictions_are_in_range():
    p = predict(CAMPAIGN)
    assert 0 < p["ltvMonths"] < 100
    assert 0 <= p["upsellProbability"] <= 1
    assert 0 <= p["superCustomerScore"] <= 100
    assert p["budgetTier"] == "Mid"


def test_the_overconfidence_caveat_fires_only_above_80():
    """Package 4 measured the model claiming 0.838 and delivering 0.761 up there."""
    p = predict(CAMPAIGN)
    assert (p["scoreCaveat"] is not None) == (p["superCustomerScore"] > 80)


def test_profit_is_labelled_as_explanatory():
    """97.7% of that model's importance needs data a planner does not have."""
    assert "not a budget forecast" in predict(CAMPAIGN)["profitUse"]


@pytest.mark.parametrize("budget,tier", [(500, "Low"), (1500, "Low"),
                                         (2000, "Mid"), (5000, "Mid"),
                                         (6000, "High"), (20000, "High")])
def test_tier_is_derived_consistently(budget, tier):
    assert predict({**CAMPAIGN, "ad_budget": budget})["budgetTier"] == tier

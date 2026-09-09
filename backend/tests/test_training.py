"""
Training a company's own models.

The thing being protected here is not accuracy, it is honesty about accuracy.
Measured on the reference data holding out a quarter, a model fitted on ten
rows beat guessing the average by 4% — inside the noise — while at fifty rows
it was 69% better. A product that shows both with the same confidence is
lying about one of them.

So every model is scored against the laziest possible alternative on the same
held-out rows, and a model that loses that comparison is marked unusable and
never shown. These tests are mostly about that gate.
"""
import numpy as np
import pandas as pd
import pytest

from app.training import (
    MIN_ROWS,
    OUTCOMES,
    features,
    load,
    predict_one,
    train,
)


@pytest.fixture(scope="module")
def rows(csv) -> list[dict]:
    """The reference CSV in the shape the database hands back."""
    df = csv.copy()
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    df["purchased"] = df["purchased"].astype(bool)
    df["upsell"] = df["upsell"].astype(bool)
    df.insert(0, "id", range(1, len(df) + 1))
    df["company_id"] = "c1"
    df["upload_id"] = "u1"
    df["budget_tier"] = "Mid"
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")


# --------------------------------------------------------------- leakage
def test_no_outcome_is_ever_a_feature(rows):
    """
    Locked rule. Predicting one outcome from another produces a model that
    scores beautifully and cannot be used, because at the moment a prediction
    is wanted none of the other outcomes exist yet.
    """
    X = features(pd.DataFrame(rows[:50]))
    assert not set(X.columns) & set(OUTCOMES)


def test_identifiers_are_not_features(rows):
    """A row id correlates with nothing and a model will still split on it."""
    X = features(pd.DataFrame(rows[:50]))
    assert not {"id", "company_id", "upload_id", "created_at"} & set(X.columns)


# ------------------------------------------------------- too little data
@pytest.mark.parametrize("n", [0, 1, 5, MIN_ROWS - 1])
def test_a_company_with_almost_no_data_gets_no_models_at_all(rows, n):
    """
    Not a weak model with a warning. Below ten rows the model does not beat
    guessing and the measurement of whether it does is itself unstable, so
    there is nothing to show and the product says so.
    """
    assert train(rows[:n]) == []


# --------------------------------------------------------- the honesty gate
@pytest.mark.parametrize("n", [MIN_ROWS, 30, 120])
def test_every_model_reports_what_it_was_measured_against(rows, n):
    for model in train(rows[:n]):
        assert model.baseline > 0
        assert model.rows > 0
        assert model.note
        # useful is exactly "beat the baseline", never a threshold on size
        assert model.useful == (model.better_by_pct > 0)


def test_a_model_no_better_than_guessing_is_marked_unusable():
    """
    Pure noise: the target is coin flips with no relationship to any feature,
    so nothing can be learned from it and the gate must fire.

    Written this way after a first attempt asserted that some target would lose
    on sixty rows of the reference data. It does on some sixty rows and not on
    others, which made the test a statement about that sample rather than about
    the code — and it would have passed or failed depending on which rows were
    taken.
    """
    rng = np.random.default_rng(7)
    n = 80
    noisy = [{
        "id": i,
        "ad_budget": int(rng.integers(500, 8000)),
        "num_leads": int(rng.integers(10, 90)),
        "leads_answered": int(rng.integers(5, 60)),
        "leads_not_answered": int(rng.integers(1, 40)),
        "followup_1": int(rng.integers(0, 30)), "followup_2": int(rng.integers(0, 20)),
        "followup_3": int(rng.integers(0, 15)), "followup_4": int(rng.integers(0, 8)),
        "followup_5": int(rng.integers(0, 4)),
        "closed": int(rng.integers(0, 10)), "not_closed": int(rng.integers(0, 20)),
        "calls_to_closed": int(rng.integers(1, 8)),
        "calls_to_not_closed": int(rng.integers(1, 8)),
        "customer_acquisition_cost": int(rng.integers(40, 300)),
        "purchased": bool(rng.integers(0, 2)),
        # None of these has anything to do with the columns above.
        "upsell": bool(rng.integers(0, 2)),
        "referred": bool(rng.integers(0, 2)),
        "ltv_months": float(rng.integers(1, 24)),
        "cumulative_profit": float(rng.integers(-5000, 15000)),
    } for i in range(n)]

    models = train(noisy)
    assert models, "models should still be fitted and measured, just not trusted"
    beaten = [m for m in models if not m.useful]
    assert beaten, "a target with no signal must fail the comparison"
    for model in beaten:
        assert model.better_by_pct <= 0
        # Kept and reported, not thrown away: the company should see that we
        # looked and found nothing, rather than see a target quietly missing.
        assert model.model_bytes and "אינו מדויק יותר" in model.note


def test_more_data_does_not_make_the_measurement_disappear(rows):
    """A large company gets the same comparison, not a bare number."""
    for model in train(rows[:400]):
        assert model.baseline > 0 and model.note


def test_the_error_is_measured_on_rows_the_model_did_not_see(rows):
    """
    Scoring on training rows reports near-zero for every company regardless of
    whether anything was learned — the one result guaranteed to mislead. A
    perfect score is therefore evidence of a bug, not of a good model.
    """
    for model in train(rows[:200]):
        assert model.score > 0


# ------------------------------------------------------- a constant column
def test_a_target_with_one_answer_is_skipped(rows):
    """
    A company where nobody ever upsold would get a model that predicts "no"
    perfectly and means nothing.
    """
    flat = [dict(r, upsell=False) for r in rows[:60]]
    assert "upsell" not in {m.target for m in train(flat)}


# --------------------------------------------------------- store and reload
def test_a_stored_model_predicts_the_same_after_a_round_trip(rows):
    """
    The model is saved as base64 and read back on another request, possibly in
    another process. A drift here would be invisible: predictions would simply
    be wrong.
    """
    import base64

    trained = next(m for m in train(rows[:200]) if m.target == "ltv_months")
    model = load(base64.b64encode(trained.model_bytes).decode(), trained.kind)
    before = predict_one(model, trained.kind, rows[0])
    after = predict_one(load(base64.b64encode(trained.model_bytes).decode(),
                             trained.kind), trained.kind, rows[0])
    assert before == after
    assert np.isfinite(before)


def test_a_new_column_in_a_later_upload_does_not_shift_the_features(rows):
    """
    A company that adds a column between one month and the next hands the model
    a different shape than it was fitted with. Reindexing onto the model's own
    feature list is what stops the values lining up against the wrong features
    — which would not raise, it would just be wrong.
    """
    import base64

    trained = next(m for m in train(rows[:200]) if m.target == "ltv_months")
    model = load(base64.b64encode(trained.model_bytes).decode(), trained.kind)
    expected = predict_one(model, trained.kind, rows[0])
    with_extra = dict(rows[0], region="north", campaign_name="spring")
    assert predict_one(model, trained.kind, with_extra) == expected

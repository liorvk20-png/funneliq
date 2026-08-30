"""
The locked rule: upsell, referred, cumulative_profit and ltv_months are
outcomes, never features for one another. features.py enforces it by
construction; this checks the construction actually holds.
"""
import pytest
from features import OUTCOMES, load_raw, target_frame


@pytest.mark.parametrize("target", OUTCOMES)
def test_no_outcome_survives_as_a_feature(target):
    X, _ = target_frame(load_raw(), target)
    leaked = [c for c in OUTCOMES if c in X.columns]
    assert leaked == [], f"predicting {target} with {leaked} as features"


def test_target_rows_with_no_answer_are_dropped():
    df = load_raw()
    X, y = target_frame(df, "ltv_months")
    assert len(X) == len(y) == 3496          # 3500 minus the 4 missing targets
    assert y.notna().all()


def test_no_row_is_lost_when_the_target_is_complete():
    """
    upsell has no missing values, so every row is usable. Worth pinning: the two
    columns that do have gaps — ltv_months and cumulative_profit — are both
    outcomes, so they are removed before a feature frame is built. The practical
    consequence is that the features never contain a null at all, and any future
    null here means a new column arrived without anyone deciding how to treat it.
    """
    X, _ = target_frame(load_raw(), "upsell")
    assert len(X) == 3500
    assert not X.isna().any().any(), "a feature column has become nullable"


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        target_frame(load_raw(), "ad_budget")

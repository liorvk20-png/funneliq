"""The dataset itself. If these fail, every number downstream is wrong."""


def test_row_count(csv):
    assert len(csv) == 3500


def test_missing_values_are_exactly_the_known_ones(csv):
    """
    Not a tautology: the loader and the analysis both branch on these counts,
    and a silently re-saved CSV with imputed values would change every model's
    training set without changing a line of code.
    """
    missing = csv.isna().sum()
    assert missing["ltv_months"] == 4
    assert missing["cumulative_profit"] == 29
    assert missing.drop(["ltv_months", "cumulative_profit"]).sum() == 0


def test_columns_match_the_schema(csv):
    assert list(csv.columns) == [
        "ad_budget", "num_leads", "leads_answered", "leads_not_answered",
        "followup_1", "followup_2", "followup_3", "followup_4", "followup_5",
        "not_closed", "closed", "calls_to_closed", "calls_to_not_closed",
        "customer_acquisition_cost", "ltv_months", "purchased", "upsell",
        "cumulative_profit", "referred",
    ]


def test_referred_is_yes_no(csv):
    """The loader maps these strings; anything else becomes a silent NULL."""
    assert set(csv["referred"].unique()) == {"Yes", "No"}


def test_binary_columns_are_zero_one(csv):
    for col in ("purchased", "upsell"):
        assert set(csv[col].unique()) <= {0, 1}

"""
The identity the whole decomposition rests on.

If mix + rate + interaction does not equal the change, every share computed
from them apportions blame that has no arithmetic behind it — and it does so
invisibly, because each component still looks like a plausible number. This is
the one property that has to hold on every dataset, not merely on the one it
was developed against.
"""
import random

import pandas as pd
import pytest

from app.analytics.contribution import (
    INTERACTION_UNSTABLE,
    Segment,
    additive_contributions,
    decompose,
)

TOLERANCE = 1e-9


def _segments(rows):
    return [Segment(*row) for row in rows]


HAND_BUILT = _segments([
    ("low", 40, 400, 60, 500),
    ("mid", 90, 500, 70, 400),
    ("high", 12, 300, 20, 200),
])

# A segment that exists in only one period, which is where a naive
# implementation divides by zero or drops the row and stops adding up.
APPEARING = _segments([
    ("stable", 50, 500, 55, 500),
    ("new", 30, 200, 0, 0),
])

VANISHING = _segments([
    ("stable", 50, 500, 55, 500),
    ("gone", 0, 0, 30, 200),
])


@pytest.mark.parametrize("segments,name", [
    (HAND_BUILT, "hand built"),
    (APPEARING, "segment appears"),
    (VANISHING, "segment vanishes"),
])
def test_the_three_components_sum_to_the_change(segments, name):
    d = decompose(segments)
    assert abs((d.mix + d.rate + d.interaction) - d.delta) < TOLERANCE, name


def test_the_identity_holds_on_the_reference_dataset(csv):
    """The real file, split in two and grouped the way the product groups it."""
    df = csv.copy()
    df["budget_tier"] = df["ad_budget"].map(
        lambda b: "Low" if b <= 1500 else ("Mid" if b <= 5000 else "High"))
    half = len(df) // 2
    current, baseline = df.iloc[:half], df.iloc[half:]

    segments = []
    for tier in ("Low", "Mid", "High"):
        c, b = current[current.budget_tier == tier], baseline[baseline.budget_tier == tier]
        segments.append(Segment(tier,
                                float(c.leads_answered.sum()), float(c.num_leads.sum()),
                                float(b.leads_answered.sum()), float(b.num_leads.sum())))
    d = decompose(segments)
    assert abs((d.mix + d.rate + d.interaction) - d.delta) < TOLERANCE


@pytest.mark.parametrize("seed", range(25))
def test_the_identity_holds_on_random_data(seed):
    """
    Twenty-five generated shapes, including ones no real export would produce.
    A decomposition that only balances on tidy inputs is a decomposition that
    will stop balancing on a customer's first unusual month.
    """
    rng = random.Random(seed)
    segments = [
        Segment(
            f"s{i}",
            numerator_current=rng.uniform(0, 500),
            denominator_current=rng.uniform(1, 2000),
            numerator_baseline=rng.uniform(0, 500),
            denominator_baseline=rng.uniform(1, 2000),
        )
        for i in range(rng.randint(2, 12))
    ]
    d = decompose(segments)
    assert abs((d.mix + d.rate + d.interaction) - d.delta) < TOLERANCE


def test_per_segment_effects_sum_to_the_totals():
    """Each component's total must be the sum of its parts, or the driver
    sentences and the mix sentence would describe different arithmetic."""
    d = decompose(HAND_BUILT)
    assert abs(sum(s.mix for s in d.segments) - d.mix) < TOLERANCE
    assert abs(sum(s.rate for s in d.segments) - d.rate) < TOLERANCE
    assert abs(sum(s.interaction for s in d.segments) - d.interaction) < TOLERANCE


def test_a_large_interaction_is_reported_as_unstable():
    """
    Weights and rates both moving hard means neither owns the change. The
    engine must know that, because the narrative refuses a causal story on it.
    """
    both_moved = _segments([("a", 90, 900, 10, 200), ("b", 5, 100, 60, 800)])
    d = decompose(both_moved)
    assert abs(d.interaction) > INTERACTION_UNSTABLE * abs(d.delta)
    assert d.unstable


def test_a_period_with_no_volume_is_refused():
    with pytest.raises(ValueError, match="non-zero denominator"):
        decompose(_segments([("a", 0, 0, 10, 100)]))


def test_additive_contributions_cover_segments_present_on_one_side_only():
    """A segment that appeared contributed all of itself; dropping it would
    leave the parts summing to less than the whole."""
    out = additive_contributions({"a": 10, "new": 5}, {"a": 4, "gone": 7})
    assert out == {"a": 6, "new": 5, "gone": -7}
    assert abs(sum(out.values()) - ((10 + 5) - (4 + 7))) < TOLERANCE


def test_pandas_grouping_matches_the_decomposition(csv):
    """Guards the join between the frame and the maths, not the maths."""
    df = pd.DataFrame({"g": ["a", "a", "b"], "num": [1, 2, 3], "den": [10, 10, 30]})
    grouped = df.groupby("g", observed=True)[["num", "den"]].sum()
    segments = [Segment(str(k), float(r.num), float(r.den), float(r.num), float(r.den))
                for k, r in grouped.iterrows()]
    d = decompose(segments)
    assert abs(d.delta) < TOLERANCE and abs(d.mix) < TOLERANCE

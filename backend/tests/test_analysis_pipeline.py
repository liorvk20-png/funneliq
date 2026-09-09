"""
The pipeline, on the reference dataset, against the stated budget.

The ten-second budget is not decoration. Analysis runs inside the upload
request, so exceeding it turns a feature into a queue, a status to poll, and a
workspace whose data and narrative can disagree. The first version of this took
sixteen seconds because the bootstrap resampled in a Python loop.
"""
import time

import numpy as np
import pandas as pd
import pytest

from app.analytics.pipeline import DIMENSIONS, add_volume_bands, analyse, prepare


@pytest.fixture(scope="module")
def periods(csv):
    """Two comparable halves, with a shift planted in one of them."""
    df = csv.copy()
    df["referred"] = df["referred"].map({"Yes": True, "No": False})
    df.insert(0, "id", range(1, len(df) + 1))
    rows = df.astype(object).where(pd.notnull(df), None).to_dict("records")
    order = np.random.default_rng(5).permutation(len(rows))
    baseline = [rows[i] for i in order[:1600]]
    current = [dict(rows[i]) for i in order[1600:3200]]
    for row in current:
        if row["ad_budget"] > 5000:
            row["leads_answered"] = int(row["leads_answered"] * 0.7)
    return current, baseline


def test_a_full_run_fits_inside_the_upload_request(periods):
    current, baseline = periods
    start = time.time()
    result = analyse(current, baseline)
    elapsed = time.time() - start
    assert result.sentences
    assert elapsed < 10, f"took {elapsed:.1f}s at depth {len(DIMENSIONS)}"


def test_the_same_input_produces_the_same_report(periods):
    current, baseline = periods
    first = [(s.section, s.rule_key, s.text_he) for s in analyse(current, baseline).sentences]
    second = [(s.section, s.rule_key, s.text_he) for s in analyse(current, baseline).sentences]
    assert first == second


def test_a_first_upload_says_there_is_nothing_to_compare_against(periods):
    """
    Every new customer sees this. Comparing against an empty baseline would
    report every metric as having risen from zero, which is arithmetic nobody
    asked for and a sentence nobody can act on.
    """
    current, _ = periods
    result = analyse(current[:300], [])
    assert result.sentences
    assert any("אין תקופת בסיס" in s.text_he for s in result.sentences)
    assert all(f.delta_pct is None for f in result.findings)


def test_no_data_produces_no_claims():
    result = analyse([], [])
    assert result.findings == [] and result.sentences == []


def test_volume_bands_are_cut_once_over_both_periods(periods):
    """
    Bands recomputed per period would move under the comparison, so a campaign
    could change band without changing size — and the mix decomposition would
    report a shift that never happened.
    """
    current, baseline = periods
    c, b = prepare(current), prepare(baseline)
    add_volume_bands(c, b)
    combined = pd.concat([c["leads"], b["leads"]])
    low, high = combined.quantile([1 / 3, 2 / 3])
    for frame in (c, b):
        small = frame[frame["lead_volume_band"] == "small"]["leads"]
        large = frame[frame["lead_volume_band"] == "large"]["leads"]
        assert small.empty or small.max() <= low
        assert large.empty or large.min() > high


def test_identical_periods_produce_no_change(periods):
    """A month compared against itself has moved by nothing, and the report
    must not manufacture a driver out of rounding."""
    current, _ = periods
    result = analyse(current, [dict(r) for r in current])
    for finding in result.findings:
        if finding.delta_abs is not None:
            assert abs(finding.delta_abs) < 1e-9


def test_every_sentence_names_a_finding_that_was_produced(periods):
    current, baseline = periods
    result = analyse(current, baseline)
    ids = {str(f.finding_id) for f in result.findings}
    assert result.sentences
    assert all(s.finding_id in ids for s in result.sentences)

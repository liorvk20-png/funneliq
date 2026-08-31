"""
How loudly a finding should speak.

Four things decide it, and the weights are the product's editorial judgement
made explicit rather than left to whoever writes the next sort key: how much of
the movement this finding accounts for, how large the movement was, how well
evidenced it is, and whether it is bad news.

Bad news outranks good news of the same size on purpose. A report has room for
a handful of sentences, and a problem the reader can still act on is worth more
of that room than a success that has already happened.
"""
from __future__ import annotations

from app.analytics.significance import CONFIDENCE_WEIGHT

WEIGHTS = {"contribution": 0.45, "magnitude": 0.30, "confidence": 0.15, "unfavourable": 0.10}

# The point at which a percentage change stops earning more severity. Beyond a
# 50% move the difference between large and enormous no longer changes what
# anyone does about it.
MAGNITUDE_CAP = 0.50


def severity(
    *, contribution_share: float | None, delta_pct: float | None,
    confidence_label: str, is_favorable: bool | None,
) -> int:
    """
    A score from 0 to 100.

    `insufficient` evidence zeroes the confidence term but does not zero the
    score, and that is deliberate: the finding still surfaces, in the quality
    section, saying that it cannot be concluded from. Dropping it would hide
    the fact that a segment moved sharply and nobody can yet say whether it
    means anything.
    """
    contribution = min(abs(contribution_share or 0.0), 1.0)
    magnitude = min(abs(delta_pct or 0.0) / MAGNITUDE_CAP, 1.0)
    confidence = CONFIDENCE_WEIGHT.get(confidence_label, 0.0)
    # Unknown favourability is treated as bad news: an unclassified movement is
    # worth a look, and the cost of a needless look is lower than of a missed
    # problem.
    unfavourable = 0.4 if is_favorable else 1.0

    return round(100 * (
        WEIGHTS["contribution"] * contribution
        + WEIGHTS["magnitude"] * magnitude
        + WEIGHTS["confidence"] * confidence
        + WEIGHTS["unfavourable"] * unfavourable
    ))

"""
Multiplicative metrics, split into additive factors.

Cost per acquisition is cost per click divided by conversion rate. When it
rises, the only question anyone actually asks is which half did it: did clicks
get more expensive, or did conversion get weaker? The two have nothing in
common as responses — one is a bidding problem, the other a landing-page
problem — and the headline number cannot tell them apart.

Logs turn the product into a sum, so the factors become additive shares that
account for the whole change:

    CPA = CPC / CVR
    ln(CPA₁/CPA₀) = ln(CPC₁/CPC₀) - ln(CVR₁/CVR₀)

    Conversions = Impressions * CTR * CVR
    ln(Conv₁/Conv₀) = ln(Imp₁/Imp₀) + ln(CTR₁/CTR₀) + ln(CVR₁/CVR₀)

Everything here is defined on ratios, so a zero or a negative on either side
has no logarithm and no meaning. Those cases raise rather than return a number
nobody can interpret.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Factor:
    key: str
    current: float
    baseline: float
    sign: int          # +1 if the factor multiplies the result, -1 if it divides
    log_change: float  # sign · ln(current / baseline)

    @property
    def pct_change(self) -> float:
        return self.current / self.baseline - 1.0


@dataclass(frozen=True)
class FactorDecomposition:
    metric_key: str
    current: float
    baseline: float
    log_change: float
    factors: tuple[Factor, ...]

    @property
    def pct_change(self) -> float:
        return self.current / self.baseline - 1.0

    def share(self, factor: Factor) -> float:
        """
        How much of the movement this factor accounts for.

        Shares are taken on the log scale, where they are additive and sum to
        one. Two factors pushing in opposite directions produce shares outside
        0..1, which is correct and worth showing: it is the arithmetic saying
        one factor moved the metric further than it actually went and another
        pulled it back.
        """
        return factor.log_change / self.log_change if self.log_change else 0.0

    @property
    def dominant(self) -> Factor:
        return max(self.factors, key=lambda f: abs(f.log_change))


def decompose_ratio(
    metric_key: str,
    current: float,
    baseline: float,
    factors: dict[str, tuple[float, float, int]],
) -> FactorDecomposition:
    """
    factors maps a name to (current, baseline, sign), where sign is +1 for a
    factor in the numerator and -1 for one in the denominator.

    The reconstructed change is checked against the metric's own change rather
    than trusted. A factor list that does not multiply back to the metric is a
    specification error — a missing term, or a sign the wrong way round — and it
    would otherwise surface as a plausible, wrong attribution.
    """
    for name, (cur, base, sign) in factors.items():
        if cur <= 0 or base <= 0:
            raise ValueError(
                f"Factor {name!r} is {cur} against {base}. A multiplicative "
                "decomposition has no meaning through zero or a negative."
            )
        if sign not in (1, -1):
            raise ValueError(f"Factor {name!r} has sign {sign}; expected 1 or -1.")
    if current <= 0 or baseline <= 0:
        raise ValueError(f"{metric_key} is {current} against {baseline}; both must be positive.")

    parts = tuple(
        Factor(key=name, current=cur, baseline=base, sign=sign,
               log_change=sign * math.log(cur / base))
        for name, (cur, base, sign) in factors.items()
    )
    observed = math.log(current / baseline)
    rebuilt = sum(f.log_change for f in parts)
    if not math.isclose(observed, rebuilt, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(
            f"The factors for {metric_key} do not reconstruct it: the metric moved "
            f"{observed:.6f} in logs and the factors account for {rebuilt:.6f}. "
            "A term is missing or a sign is inverted."
        )
    return FactorDecomposition(metric_key, current, baseline, observed, parts)


def cost_per_acquisition(
    spend_current: float, spend_baseline: float,
    clicks_current: float, clicks_baseline: float,
    conversions_current: float, conversions_baseline: float,
) -> FactorDecomposition:
    """CPA = CPC / CVR — the question the category is actually asked."""
    cpc_c, cpc_b = spend_current / clicks_current, spend_baseline / clicks_baseline
    cvr_c, cvr_b = conversions_current / clicks_current, conversions_baseline / clicks_baseline
    return decompose_ratio(
        "cpa",
        spend_current / conversions_current,
        spend_baseline / conversions_baseline,
        {"cpc": (cpc_c, cpc_b, 1), "cvr": (cvr_c, cvr_b, -1)},
    )

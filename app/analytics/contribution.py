"""
Why a rate moved: because the mix changed, or because performance changed.

An overall rate is a weighted average of its segments, R = Σ wᵢ·rᵢ. When it
falls, two entirely different things may have happened. The segments may each
be performing exactly as before while more volume moved to the weaker ones — a
mix shift, and nothing is wrong with the segments. Or the weights may be
unchanged and the segments themselves got worse — a rate shift, and something
is. The same headline number, two opposite responses.

The decomposition is exact:

    ΔR = Σ (wᵢ¹ - wᵢ⁰)·rᵢ⁰            mix
       + Σ  wᵢ⁰·(rᵢ¹ - rᵢ⁰)            rate
       + Σ (wᵢ¹ - wᵢ⁰)·(rᵢ¹ - rᵢ⁰)     interaction

The third term is kept and reported rather than folded into one of the others.
Absorbing it is the standard shortcut and it is how a decomposition quietly
starts lying: when both weights and rates move together the interaction is
where that fact lives, and hiding it produces a confident mix-versus-rate
verdict precisely when no such verdict is available.
"""
from __future__ import annotations

from dataclasses import dataclass

# Above this share of the total change, the split between mix and rate is not
# clean enough to describe in words. The engine downgrades confidence and the
# narrative is barred from causal phrasing.
INTERACTION_UNSTABLE = 0.25


@dataclass(frozen=True)
class Segment:
    """One slice of the population, in both periods."""
    key: str
    numerator_current: float
    denominator_current: float
    numerator_baseline: float
    denominator_baseline: float

    @property
    def rate_current(self) -> float:
        d = self.denominator_current
        return self.numerator_current / d if d else 0.0

    @property
    def rate_baseline(self) -> float:
        d = self.denominator_baseline
        return self.numerator_baseline / d if d else 0.0


@dataclass(frozen=True)
class SegmentEffect:
    key: str
    mix: float
    rate: float
    interaction: float
    weight_current: float
    weight_baseline: float
    rate_current: float
    rate_baseline: float

    @property
    def total(self) -> float:
        return self.mix + self.rate + self.interaction


@dataclass(frozen=True)
class Decomposition:
    rate_current: float
    rate_baseline: float
    delta: float
    mix: float
    rate: float
    interaction: float
    segments: tuple[SegmentEffect, ...]

    @property
    def unstable(self) -> bool:
        """
        True when the interaction is too large for a mix-or-rate story.

        A change of nearly zero is also unstable: dividing a small interaction
        by a smaller total produces an enormous ratio, and "the decomposition is
        unclear" is the honest reading of a rate that did not move.
        """
        if self.delta == 0:
            return self.interaction != 0
        return abs(self.interaction) > INTERACTION_UNSTABLE * abs(self.delta)

    def share(self, component: float) -> float:
        return component / self.delta if self.delta else 0.0


def decompose(segments: list[Segment]) -> Decomposition:
    """
    Split the change in an overall rate into mix, rate and interaction.

    The three components sum to the change exactly, by construction rather than
    by rounding: every term is written out and nothing is estimated. A test
    asserts the identity to 1e-9 on real and generated data, because a
    decomposition that does not add up is worse than no decomposition — it
    apportions blame that has no arithmetic behind it.
    """
    total_current = sum(s.denominator_current for s in segments)
    total_baseline = sum(s.denominator_baseline for s in segments)
    if not total_current or not total_baseline:
        raise ValueError(
            "Both periods need a non-zero denominator before a rate can be "
            "compared. A period with no volume has no rate to decompose."
        )

    effects: list[SegmentEffect] = []
    for s in segments:
        w1 = s.denominator_current / total_current
        w0 = s.denominator_baseline / total_baseline
        r1, r0 = s.rate_current, s.rate_baseline
        effects.append(SegmentEffect(
            key=s.key,
            mix=(w1 - w0) * r0,
            rate=w0 * (r1 - r0),
            interaction=(w1 - w0) * (r1 - r0),
            weight_current=w1, weight_baseline=w0,
            rate_current=r1, rate_baseline=r0,
        ))

    rate_current = sum(s.numerator_current for s in segments) / total_current
    rate_baseline = sum(s.numerator_baseline for s in segments) / total_baseline
    return Decomposition(
        rate_current=rate_current,
        rate_baseline=rate_baseline,
        delta=rate_current - rate_baseline,
        mix=sum(e.mix for e in effects),
        rate=sum(e.rate for e in effects),
        interaction=sum(e.interaction for e in effects),
        segments=tuple(effects),
    )


def additive_contributions(
    current: dict[str, float], baseline: dict[str, float]
) -> dict[str, float]:
    """
    For a total that is a sum rather than an average, a segment's contribution
    is simply how much it changed. Keys absent from one side count as zero:
    a segment that appeared or vanished contributed its whole value, and
    dropping it would leave the parts not summing to the whole.
    """
    return {k: current.get(k, 0.0) - baseline.get(k, 0.0)
            for k in set(current) | set(baseline)}

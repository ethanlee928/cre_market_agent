"""Deterministic signal detection.

This is the spine half of Approach C. Detectors are plain Python: same input,
same output, every run, and unit-testable so the numbers can be defended in a
meeting. The agent's job is to interpret what these produce, never to compute it.

Severity is named, not coloured (review finding: colour-only encoding fails
colour-blind users, and GREEN on "record demand" reads as "ignore me" when it is
the best line to carry into a client meeting):

    RISK        something is moving against you
    WATCH       something is building that will matter
    OPPORTUNITY something is moving in your favour
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .store import Fact, Store

Severity = str
RISK, WATCH, OPPORTUNITY = "RISK", "WATCH", "OPPORTUNITY"

_RANK = {RISK: 0, WATCH: 1, OPPORTUNITY: 2}


@dataclass
class Signal:
    id: str
    severity: Severity
    headline: str
    detail: str
    evidence: list[Fact] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)   # watchlist asset names

    def citations(self) -> list[str]:
        return sorted({f.source.cite() for f in self.evidence})

    def sort_key(self) -> tuple:
        # Single ordering rule (review finding: three competing orders in one
        # list). Watchlist relevance first, then severity. "New since last run"
        # is a badge, never a sort key.
        return (0 if self.affected else 1, _RANK.get(self.severity, 9))


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------

def quality_spread(store: Store, watchlist=None) -> list[Signal]:
    """Grade A pulling away from Grade B: the flight-to-quality divergence.

    Null-safe by design. West End grade_b_rent_avg has no published level, only
    a -11.3% delta, and that is the headline signal. Fire on the delta.
    """
    out = []
    for sub in ("City", "West End"):
        # get_pair, not two get() calls: differencing a 2027 Grade A against a
        # 2026 Grade B would invent a spread no source published (E-8).
        pair = store.get_pair("grade_a_rent_avg", "grade_b_rent_avg", sub)
        if not pair:
            continue
        a, b = pair
        da, db = a.delta("yoy"), b.delta("yoy")
        if da is None or db is None:
            continue

        spread = da.value - db.value
        if spread < 5.0:
            continue

        b_level = b.render_value() if b.value is not None else "level not published"
        out.append(Signal(
            id=f"quality_spread:{sub}",
            severity=RISK,
            headline=f"{sub} Grade B rents falling while Grade A rises "
                     f"({db.render()} against {da.render()})",
            detail=(
                f"The quality gap in the {sub} widened to {spread:.1f} points over the "
                f"year to {a.period}. Grade A averages {a.render_value()} "
                f"({da.render()}), while Grade B is at {b_level} ({db.render()}). "
                f"Secondary stock needs capital to compete, and the gap is forecast "
                f"to keep widening."
            ),
            evidence=[a, b],
            affected=_match(watchlist, submarket=sub, grade="B"),
        ))
    return out


def supply_shock(store: Store, watchlist=None) -> list[Signal]:
    """A record completion year landing with most of it unlet."""
    f = store.get("completions_forecast", "Central London")
    uc = store.get("under_construction", "Central London")
    if not f:
        return []

    prelet = f.extras.get("prelet_pct")
    vs_avg = f.delta("vs_avg")
    if prelet is None or prelet >= 60:
        return []

    # The forecast year comes off the fact, never a literal. A 2027 seed used to
    # print "in 2026" because the year was baked into this string.
    detail = (
        f"Central London completions are on course for {f.render_value()} in {f.period}"
        + (f", {vs_avg.render()} against the 10-year average" if vs_avg else "")
        + f", and only {prelet:.0f}% of it is pre-let. "
    )
    if uc:
        uc_prelet = uc.extras.get("prelet_pct")
        # Under construction is measured at its own period, which need not be
        # the forecast year. Say which, rather than implying they are the same.
        detail += (
            f"A further {uc.render_value()} was under construction at {uc.period} "
            f"with {uc_prelet:.0f}% pre-let. "
        )
    detail += "New Grade A space arriving unlet puts pressure on headline rents and incentives."

    return [Signal(
        id="supply_shock:Central London",
        severity=WATCH,
        headline=f"Record {f.render_value()} of completions in {f.period}, "
                 f"only {prelet:.0f}% pre-let",
        detail=detail,
        evidence=[x for x in (f, uc) if x],
        affected=_match(watchlist, submarket=None),
    )]


def large_occupier_squeeze(store: Store, watchlist=None) -> list[Signal]:
    """More big requirements chasing large floorplates than there are options."""
    # Same-period pair (E-8): a ratio built from this year's requirements over
    # last year's options is not an imbalance anyone published.
    pair = store.get_pair("large_active_requirements", "large_grade_a_options",
                          "Central London")
    if not pair:
        return []
    reqs, opts = pair
    demand = store.get("active_demand", "Central London")
    if opts.value is None or reqs.value is None or opts.value == 0:
        return []
    if reqs.value <= opts.value:
        return []

    ratio = reqs.value / opts.value
    detail = (
        f"At {opts.period} there are {reqs.value:.0f} live requirements over 100,000 "
        f"sq ft chasing only {opts.value:.0f} available Grade A options, a "
        f"{ratio:.1f} to 1 imbalance. {opts.note or ''} "
    )
    if demand:
        d = demand.delta("vs_avg")
        # Demand is published at its own period; name it rather than folding it
        # into the sentence above and implying one measurement date.
        detail += (
            f"Active demand at {demand.period} stands at {demand.render_value()}"
            + (f", {d.render()} on the long-run average" if d else "")
            + ". Landlords of large, central, best-in-class space have pricing power."
        )

    return [Signal(
        id="large_occupier_squeeze:Central London",
        severity=OPPORTUNITY,
        headline=f"{reqs.value:.0f} large requirements chasing {opts.value:.0f} options "
                 f"({ratio:.1f}:1)",
        detail=detail.strip(),
        evidence=[x for x in (opts, reqs, demand) if x],
        affected=_match(watchlist, submarket=None, min_sqft=100_000),
    )]


DETECTORS: list[Callable[..., list[Signal]]] = [
    quality_spread,
    supply_shock,
    large_occupier_squeeze,
]


def detect_all(store: Store, watchlist=None) -> list[Signal]:
    signals: list[Signal] = []
    for d in DETECTORS:
        signals.extend(d(store, watchlist))
    signals.sort(key=lambda s: s.sort_key())
    return signals


# --------------------------------------------------------------------------

def _match(watchlist, submarket: str | None = None, grade: str | None = None,
           min_sqft: int | None = None) -> list[str]:
    """Which watchlist assets does this signal touch? Empty list is fine."""
    if not watchlist:
        return []
    return watchlist.matching(submarket=submarket, grade=grade, min_sqft=min_sqft)

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

import re
from dataclasses import dataclass, field
from typing import Callable

from .store import Fact, Store
from .watchlist import Asset

Severity = str
RISK, WATCH, OPPORTUNITY = "RISK", "WATCH", "OPPORTUNITY"

_RANK = {RISK: 0, WATCH: 1, OPPORTUNITY: 2}

# The controlled vocabulary a matched signal closes on. "monitor" is absent by
# design and asserted absent by test: it is the word that lets an analyst end a
# paragraph without deciding anything, and this product exists to decide.
# "hold" is a real answer here, reached only with its reason stated.
ACTIONS = ("regear", "refurbish", "re-price", "hold", "defer capex",
           "start the conversation")

# A pipeline period like "2026H2-2029". Read off Period.raw rather than the
# parsed object: store._RANGE discards the half marker, so Period reports
# months (1, 12) and a window that does not open until July would answer True
# for Q1. Lease dates are (year, month) tuples throughout -- see
# watchlist.parse_ym.
_PIPELINE_PERIOD = re.compile(r"^(\d{4})(H[12]|Q[1-4])?-(\d{4})$")
_WINDOW_OPENS = {"H1": 1, "H2": 7, "Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}


def _pipeline_window(fact: Fact) -> tuple[str, str] | None:
    """("2026-07", "2029-12") from a fact whose period is a published range.

    A published window, not a tolerance invented around a point estimate. The
    alternative was +/-12 months either side of completions_forecast, which
    also catches both breaks but is a margin no source ever stated.
    """
    m = _PIPELINE_PERIOD.match(fact.period.raw)
    if not m:
        return None
    start_year, marker, end_year = m.groups()
    return f"{start_year}-{_WINDOW_OPENS.get(marker or 'H1', 1):02d}", f"{end_year}-12"


@dataclass
class Signal:
    id: str
    severity: Severity
    headline: str
    detail: str
    evidence: list[Fact] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)   # watchlist asset names
    # Why this signal touches each named asset, and what to do about it. Kept
    # as two str-to-str maps rather than a dataclass: `affected` stays a list
    # of names so `a.name in s.affected` keeps working in the sidebar, and both
    # of these serialise straight into a Gemini function response, where a
    # dataclass would hit the blanket except in gemini.py and vanish.
    match_reasons: dict[str, str] = field(default_factory=dict)
    match_actions: dict[str, str] = field(default_factory=dict)

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
        sig = Signal(
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
        )
        for asset in _assets(watchlist, submarket=sub, grade="B"):
            sig.affected.append(asset.name)
            reason, action = _reversion(asset, a, b)
            sig.match_reasons[asset.name] = reason
            sig.match_actions[asset.name] = action
        out.append(sig)
    return out


def _epc_ready(asset: Asset) -> bool:
    """Is this building's EPC good enough to justify spending on it?

    C or better. Deliberately no compliance deadline in code or narrative: the
    MEES dates are unsettled, and a fabricated deadline is the same failure
    class as quoting a rent the source never published.
    """
    return bool(asset.epc_rating) and asset.epc_rating.upper() <= "C"


def _reversion(asset: Asset, grade_a: Fact, grade_b: Fact) -> tuple[str, str]:
    """What the grade spread means for one building, and what to do about it.

    Computed in Python, not asked of the model. This is the arithmetic someone
    carries into a meeting, so it has to be identical on every run and unit-
    testable line by line. A model-authored figure is neither.

    Two figures, never three. Passing-minus-Grade-B and Grade-A-minus-passing
    sum exactly to the grade gap, so rendering all three invites a reader to
    add two of them and double-count the same spread.
    """
    bits: list[str] = []
    action = "hold"
    passing, sqft = asset.passing_rent_psf, asset.sqft

    # E-4: a Fact whose value is None is still truthy, and West End
    # grade_b_rent_avg is exactly that -- Savills published the -11.3% and
    # withheld the level. The level test is `is not None`, never bare
    # truthiness, or this sentence asserts a comparison it never made.
    if passing is not None and sqft and grade_b.value is not None:
        gap = passing - grade_b.value
        bits.append(
            f"passing £{passing:,.2f} psf against the {grade_b.submarket} Grade B "
            f"average of £{grade_b.value:,.2f} psf ({grade_b.period}), which is "
            f"£{abs(gap) * sqft:,.0f} a year "
            f"{'above' if gap > 0 else 'below'} market across {sqft:,} sq ft, "
            f"exposed at the next review"
        )
        if gap > 0:
            action = "regear"
    elif grade_b.value is None:
        d = grade_b.delta("yoy")
        bits.append(
            f"no reversion figure is computable: Savills publishes the "
            f"{grade_b.submarket} Grade B change"
            + (f" ({d.render()})" if d else "")
            + " but not the level, so there is nothing to measure the passing rent against"
        )

    if passing is not None and sqft and grade_a.value is not None:
        uplift = (grade_a.value - passing) * sqft
        if uplift > 0:
            bits.append(
                f"reaching the {grade_a.submarket} Grade A average of "
                f"£{grade_a.value:,.2f} psf would be worth £{uplift:,.0f} a year gross, "
                f"before capex and voids"
            )
            if action == "hold":
                action = "refurbish" if _epc_ready(asset) else "defer capex"

    if not bits:
        bits.append("no passing rent or floor area on file, so nothing is computable "
                    "for this building beyond the market move above")
    if action == "defer capex" and asset.epc_rating:
        bits.append(f"EPC {asset.epc_rating} is below the standard several of those "
                    f"proposals would require, which comes first")
    return "; ".join(bits) + ".", action


def supply_shock(store: Store, watchlist=None) -> list[Signal]:
    """A record completion year landing with most of it unlet."""
    f = store.get("completions_forecast", "Central London")
    uc = store.get("under_construction", "Central London")
    pipeline = store.get("pipeline_to_2029", "Central London")
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
        # Under construction is measured at its own period, which need not be
        # the forecast year. Say which, rather than implying they are the same.
        #
        # prelet_pct is optional on the fact, and E-4 applies to an extra the
        # same as to a level: unpublished is absent, not zero. Formatting it
        # unguarded also raised TypeError on None, inside the detect_all that
        # app.py runs in its cached loader -- one missing field would have
        # taken the whole brief page down, not just this sentence.
        uc_prelet = uc.extras.get("prelet_pct")
        detail += (
            f"A further {uc.render_value()} was under construction at {uc.period}"
            + (f" with {uc_prelet:.0f}% pre-let. " if uc_prelet is not None
               else ", with the pre-let share not published. ")
        )
    detail += "New Grade A space arriving unlet puts pressure on headline rents and incentives."

    # Same rule for both extras. A `.get(name, 0)` default renders an
    # unpublished figure as a published zero -- "across 0 schemes, 0% pre-let"
    # reads as sourced and is invented, which is the defect the store exists
    # to prevent. Bound here rather than inside the block below, because the
    # match reason further down reads pipeline_prelet too.
    schemes = pipeline.extras.get("schemes") if pipeline else None
    pipeline_prelet = pipeline.extras.get("prelet_pct") if pipeline else None

    window = _pipeline_window(pipeline) if pipeline else None
    if window and pipeline:
        detail += f" Over {pipeline.period} the pipeline runs to {pipeline.render_value()}"
        if schemes is not None:
            detail += f" across {schemes:.0f} schemes"
        detail += (f", {pipeline_prelet:.0f}% pre-let." if pipeline_prelet is not None
                   else ", with the pre-let share not published.")

    sig = Signal(
        id="supply_shock:Central London",
        severity=WATCH,
        headline=f"Record {f.render_value()} of completions in {f.period}, "
                 f"only {prelet:.0f}% pre-let",
        detail=detail,
        evidence=[x for x in (f, uc, pipeline) if x],
    )

    # Was `_match(watchlist, submarket=None)`, which filters on nothing and so
    # claimed every asset in the portfolio -- the page asserting an exposure it
    # could not name a reason for. A market-wide signal earns a building only
    # when that building has a decision falling inside the published window.
    if window and pipeline:
        for asset in _assets(watchlist, lease_event_between=window):
            sig.affected.append(asset.name)
            event, kind = asset.lease_event(), "break" if asset.break_date else "lease expiry"
            sig.match_reasons[asset.name] = (
                f"its {kind} at {event} falls inside the {pipeline.period} delivery "
                f"window, so this is the market it would be re-letting into, against "
                f"{pipeline.render_value()} of new space"
                + (f" that is {pipeline_prelet:.0f}% pre-let."
                   if pipeline_prelet is not None
                   else " whose pre-let share this source does not publish.")
            )
            sig.match_actions[asset.name] = "start the conversation"
    return [sig]


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

    sig = Signal(
        id="large_occupier_squeeze:Central London",
        severity=OPPORTUNITY,
        headline=f"{reqs.value:.0f} large requirements chasing {opts.value:.0f} options "
                 f"({ratio:.1f}:1)",
        detail=detail.strip(),
        evidence=[x for x in (opts, reqs, demand) if x],
    )
    for asset in _assets(watchlist, min_sqft=100_000):
        sig.affected.append(asset.name)
        sig.match_reasons[asset.name] = (
            f"at {asset.sqft:,} sq ft it is one of the large floorplates in short "
            f"supply: {reqs.value:.0f} live requirements against {opts.value:.0f} "
            f"available options is pricing power, and nothing about it needs acting "
            f"on before the next review."
        )
        sig.match_actions[asset.name] = "hold"
    return [sig]


def sector_demand(store: Store, watchlist=None) -> list[Signal]:
    """A sector whose completed deals look weak while its pipeline says otherwise.

    Take-up is a trailing number: deals already signed. Space under offer is
    leading: deals about to sign. When a sector posts its weakest take-up in
    years while holding an outsized share of what is under offer, the weak
    figure is the old news and the pipeline is the story. That is the sector
    to point a leasing campaign at.

    This conclusion appears nowhere in the source. It needs two of its tables
    read against each other -- the sector take-up breakdown and the
    under-offer total -- which is the difference between summarising a report
    and reading a market.

    affected stays empty on purpose. Sectors are occupier groupings, not
    geographies, so no watchlist asset is genuinely touched by this. Claiming
    otherwise would inflate the "N of M signals touch your portfolio" count
    with a signal that does not.
    """
    total = store.get("under_offer", "Central London")
    out = []
    for f in store.find(metric="take_up", sector="__any__"):
        if not f.sector:
            continue
        taken = f.extras.get("share_pct")
        pipeline = f.extras.get("share_of_under_offer_pct")
        trailing = f.delta("vs_avg")
        if taken is None or pipeline is None or trailing is None:
            continue
        if trailing.value >= 0 or pipeline < taken * 1.5:
            continue

        weight = pipeline / taken
        detail = (
            f"{f.sector} take-up ran to {f.render_value()} in {f.period}, "
            f"{trailing.render()} against its {trailing.basis.replace('_', ' ')} "
            f"and the weakest in the series. Read alone that is a sector in "
            f"retreat. But it accounts for {pipeline:.0f}% of space currently "
            f"under offer while making up only {taken:.0f}% of take-up "
            f"-- {weight:.1f} times its completed weight"
        )
        if total:
            detail += f", against a Central London total of {total.render_value()}"
        detail += (
            ". Take-up counts deals already signed; under offer counts deals "
            "about to sign. The two disagree, and the leading number is the "
            "one that has not happened yet. Point the next leasing campaign "
            "here rather than at the sectors already posting records."
        )

        out.append((weight, Signal(
            id=f"sector_demand:{f.sector}",
            severity=OPPORTUNITY,
            headline=f"{f.sector} take-up at a series low, yet {pipeline:.0f}% of "
                     f"space under offer against {taken:.0f}% of take-up",
            detail=detail,
            evidence=[x for x in (f, total) if x],
            affected=[],
        )))

    # Rank on the computed weight, never by reading a number back out of the
    # headline. Parsing prose for a figure is the defect this store exists to
    # prevent, and it does not stop being one because the prose is ours.
    out.sort(key=lambda pair: -pair[0])
    return [sig for _, sig in out[:1]]


def peer_gap(store: Store, watchlist=None) -> list[Signal]:
    """A holding against named peer buildings: aligned, expensive, or cheap.

    The industry reviewer's question, answered at the granularity they asked
    for. comps.compare assembles the peer set and the benchmarks; this
    detector chooses the severity and the verb, next to ACTIONS, and renders
    the table. A Refusal emits nothing: a comparison that cannot name its
    peers does not fire, the same rule as an exposure a signal cannot justify.
    """
    from . import comps

    out = []
    for asset in (watchlist.assets if watchlist else []):
        c = comps.compare(asset, store)
        if isinstance(c, comps.Refusal):
            continue
        severity, reason, action = _peer_verdict(asset, c)

        if c.valuation_gap_share is not None:
            share = c.valuation_gap_share
            direction = ("above" if share > 0 else "below") \
                if abs(share) > ALIGNED_BAND else "in line with"
            pct = f"{abs(share) * 100:.0f}% " if direction != "in line with" else ""
            headline = (f"{asset.name} valued {pct}{direction} its "
                        f"{c.valuation_n}-peer street "
                        f"(£{c.asset_value_psm:,.0f} against "
                        f"£{c.valuation_avg_psm:,.0f} per m²)")
        else:
            headline = (f"{asset.name} against {len(c.peers)} named peers: "
                        f"no verdict computable")

        sig = Signal(
            id=f"peer_gap:{asset.name}",
            severity=severity,
            headline=headline,
            detail=_peer_table(asset, c),
            evidence=list(c.evidence),
            affected=[asset.name],
        )
        sig.match_reasons[asset.name] = reason
        sig.match_actions[asset.name] = action
        out.append(sig)
    return out


# Within this band of the valuation median, passing is "in line": no lease
# event should be forced by a gap smaller than a normal negotiation range.
ALIGNED_BAND = 0.05


def _peer_verdict(asset: Asset, c) -> tuple[Severity, str, str]:
    """Severity, reason sentence and decision verb for one comparison.

    Computed here, beside ACTIONS, for the same reason as _reversion: this is
    the arithmetic someone carries into a meeting. Like for like only (C-3):
    the verdict rides on valuation-against-valuation, with the contract-rent
    comparison as the second figure. Two figures, never three -- no cross-
    basis difference is ever rendered, because a passing rent minus a
    valuation is a number no source supports.
    """
    if c.asset_value_psm is None:
        return WATCH, ("no valuation on file for this building, so the "
                       "like-for-like street comparison cannot run; the peer "
                       "table above is the answer."), "hold"
    if c.valuation_avg_psm is None:
        return WATCH, (f"no verdict is computable: {c.valuation_refusal}."), "hold"

    share = c.valuation_gap_share
    bits = [
        f"valued at £{c.asset_value_psm:,.2f} per m² against a "
        f"£{c.valuation_avg_psm:,.2f} per m² median across {c.valuation_n} "
        f"named peer buildings, like for like on the VOA "
        f"{c.valuation_period} valuation basis"
    ]
    if c.valuation_gap_annual is not None:
        bits.append(f"£{abs(c.valuation_gap_annual):,.0f} a year of implied "
                    f"rental value {'above' if share > 0 else 'below'} the "
                    f"street across {asset.sqft:,} sq ft")
    if c.achieved_psf is not None and asset.passing_rent_psf is not None:
        bits.append(f"on contract terms, passing £{asset.passing_rent_psf:,.2f} "
                    f"psf against a £{c.achieved_psf:,.2f} psf median over "
                    f"{c.achieved_n} reported peer lettings")
    elif c.achieved_refusal:
        bits.append(c.achieved_refusal)

    if share > ALIGNED_BAND:
        return RISK, "; ".join(bits) + (
            ". A building priced above its street is the one a tenant "
            "renegotiates first."), "regear"
    if share < -ALIGNED_BAND:
        return OPPORTUNITY, "; ".join(bits) + (
            ". Priced below the street is reversion waiting for a review "
            "date."), "re-price"
    return WATCH, "; ".join(bits) + (
        ". Within the band a negotiation would move it either way."), "hold"


def _peer_table(asset: Asset, c) -> str:
    """The comparison table, in markdown, every cell computed or refused.

    The asset's own row labels where its figures come from: a valuation off
    the same VOA list as the peers' needs no flag, but a figure the user
    typed into the yaml sits beside real buildings' real figures and reads as
    more authoritative than anywhere else it renders, so the label rides in
    the same row as the number (the honesty guarantee, applied where it is
    under the most load). Passing rents are always the user's own data.
    """
    def cell(v, fmt="{}"):
        return fmt.format(v) if v is not None else "not published"

    if c.asset_value_psm is None:
        own_val = "not published"
    elif c.asset_value_from_store:
        own_val = f"£{c.asset_value_psm:,.2f}/m²"
    else:
        own_val = f"£{c.asset_value_psm:,.2f}/m² (your figure)"
    passing = (f"passing £{asset.passing_rent_psf:,.2f} psf (your figure)"
               if asset.passing_rent_psf is not None else "no passing rent on file")

    lines = [
        "| Building | Size (sq ft) | Built | EPC | VOA valuation* | Reported rent |",
        "|---|---|---|---|---|---|",
        f"| **{asset.name}** (yours) | {cell(asset.sqft, '{:,}')} "
        f"| {cell(asset.year_built)} | {cell(asset.epc_rating)} "
        f"| {own_val} "
        f"| {passing} |",
    ]
    for row in c.rows:
        lines.append(
            f"| {row['peer']} | {cell(row['sqft'], '{:,}')} "
            f"| {cell(row['year_built'])} | {cell(row['epc'])} "
            f"| {cell(row['valuation_psm'], '£{:,.2f}/m²')} "
            f"| {cell(row['reported_rent_psf'], '£{:,.2f} psf')} |")

    rules = ("Peers qualify on: same submarket; floor area within ×/÷2"
             + ("; completion year within ±10 years."
                if c.age_rule_applied else
                ". The age rule did not run: the asset declares no completion "
                "year."))
    lines.append("")
    lines.append(
        f"{rules} \\*Valuations are VOA rateable values — each building's "
        f"aggregate rateable value over aggregate floor area across its "
        f"office hereditaments — on a {c.valuation_period or 'fixed-date'} "
        f"valuation basis. They sit below headline rents by construction and "
        f"are compared only with each other, never with a passing rent.")
    return "\n".join(lines)


DETECTORS: list[Callable[..., list[Signal]]] = [
    peer_gap,
    quality_spread,
    supply_shock,
    large_occupier_squeeze,
    sector_demand,
]


def detect_all(store: Store, watchlist=None) -> list[Signal]:
    signals: list[Signal] = []
    for d in DETECTORS:
        signals.extend(d(store, watchlist))
    signals.sort(key=lambda s: s.sort_key())
    return signals


# --------------------------------------------------------------------------

def _assets(watchlist, submarket: str | None = None, grade: str | None = None,
            min_sqft: int | None = None,
            lease_event_between: tuple[str, str] | None = None) -> list[Asset]:
    """Which watchlist assets does this signal touch? Empty list is fine.

    Returns Assets rather than names because every caller now builds a reason
    from the building's own figures. An empty or absent watchlist returns [],
    which is the market-wide base case and must stay fully functional.
    """
    if not watchlist:
        return []
    return watchlist.matching(submarket=submarket, grade=grade, min_sqft=min_sqft,
                              lease_event_between=lease_event_between)

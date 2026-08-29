"""Building versus building: the peer set and the arithmetic over it.

The reviewer's question -- "is this holding aligned with the market, more
expensive, or cheaper?" -- answered against named buildings rather than a
submarket average. `_reversion` already does this arithmetic against a grade
average; this module swaps the comparator for a deliberately assembled peer
set.

Two invariants live here:

  C-1  £/m² never renders as psf. The VOA publishes rateable values per square
       metre; `psm_to_psf` is the only conversion site and a test pins the
       constant. Same defect class as the bps-as-pct trap (E-5): silently
       ~11x wrong, with no exception to catch it.
  C-2  A thin peer set refuses; it never averages. Two buildings are an
       anecdote wearing a median's clothes. Below the floors declared here,
       the answer is the refusal sentence -- computed, rendered, and honest --
       not a statistic over whatever happened to match.

Everything here is plain Python: same input, same output, every run. The
model narrates a Comparison; it never assembles one. Severity and the decision
verb are chosen in signals.peer_gap, next to ACTIONS, so this module cannot
drift from the closed vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .store import Building, Fact, Store
from .watchlist import Asset

# Exact by definition: 1 ft = 0.3048 m, so 1 ft² = 0.09290304 m². Multiplying
# a £/m² figure by this yields £/ft². The ONLY conversion site (C-1).
PSF_PER_PSM = 0.09290304

MIN_PEERS = 3        # fewer qualifying buildings than this refuses (C-2)
MIN_VALUATIONS = 3   # fewer published valuations than this refuses the median
MIN_LETTINGS = 2     # fewer reported rents than this refuses that benchmark
SIZE_BAND = 2.0      # peer floor area within ×/÷ this of the asset's
AGE_BAND_YEARS = 10  # peer completion year within ± this of the asset's

VALUATION_METRIC = "rateable_value_avg"


def psm_to_psf(value_psm: float) -> float:
    """£ per square metre to £ per square foot. Nowhere else converts."""
    return value_psm * PSF_PER_PSM


@dataclass(frozen=True)
class PeerMatch:
    """A building admitted to the peer set, with the reasons it qualified.

    `reasons` is non-empty by construction: a rule either passes and
    contributes its sentence, or the building is excluded. A peer this
    comparison cannot justify is the same defect as an asset a signal cannot
    justify claiming.
    """
    building: Building
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Refusal:
    """The computed answer when the honest answer is no answer."""
    reason: str


@dataclass
class Comparison:
    """One asset against its named peers. All figures computed here, none
    generated; the caller renders, the model narrates.

    C-3, learned from the harvest: comparisons run like for like, never across
    bases. VOA valuations sit systematically below headline rents (they are
    fixed-date, net-basis assessments), so the asset's own valuation is
    compared to the peers' valuations, and its passing rent to peers' reported
    letting rents. A passing rent against a valuation median would be a
    labelled number and still a misleading one.
    """
    asset_name: str
    submarket: str
    peers: list[PeerMatch]
    age_rule_applied: bool          # False when the asset declares no year_built
    rows: list[dict] = field(default_factory=list)   # per-peer render/serialise rows

    # Like-for-like 1: valuation vs valuation, both £/m², same fixed date.
    valuation_avg_psm: float | None = None  # median of per-building weighted avgs
    valuation_n: int = 0
    valuation_period: str = ""              # the valuation basis year, off the facts
    valuation_refusal: str | None = None
    asset_value_psm: float | None = None    # the asset's own valuation
    asset_value_from_store: bool = False    # True: same VOA list as the peers'
                                            # False: user-supplied via the yaml
    valuation_gap_share: float | None = None    # (asset - street) / street
    valuation_gap_annual: float | None = None   # implied £/yr across asset area

    # Like-for-like 2: contract rent vs contract rents, both £ psf.
    achieved_psf: float | None = None    # median reported letting rent among peers
    achieved_n: int = 0
    achieved_refusal: str | None = None
    passing_vs_achieved_psf: float | None = None

    evidence: list[Fact] = field(default_factory=list)


def peer_set(asset: Asset, store: Store) -> list[PeerMatch] | Refusal:
    """The buildings this asset may honestly be compared against.

    Rules are conjunctive and each contributes a reason. A rule whose input is
    missing on the *candidate* excludes the candidate -- a peer that cannot
    show its size has no business in a size-banded set. A rule whose input is
    missing on the *asset* is skipped entirely and reported via
    `age_rule_applied`, because silently passing everyone is a filter on
    nothing, the supply_shock defect.
    """
    if not asset.sqft:
        return Refusal(
            f"{asset.name} has no floor area on file, so the size rule cannot "
            f"run and no peer set can be justified")
    roster = store.find_buildings(asset.submarket)
    if not roster:
        have = sorted({b.submarket for b in store.buildings})
        rosters = " and ".join(have) if have else "no submarket at all"
        return Refusal(
            f"no building roster covers {asset.submarket}: this system holds "
            f"rosters for {rosters} only, so there is nothing to compare "
            f"against")

    matches: list[PeerMatch] = []
    for b in roster:
        if b.name == asset.name:
            continue
        reasons = [f"same submarket ({b.submarket})"]
        if not b.sqft or not (1 / SIZE_BAND <= b.sqft / asset.sqft <= SIZE_BAND):
            continue
        reasons.append(f"{b.sqft:,} sq ft against your {asset.sqft:,} "
                       f"(within ×/÷{SIZE_BAND:g})")
        if asset.year_built is not None:
            if b.year_built is None:
                continue
            if abs(b.year_built - asset.year_built) > AGE_BAND_YEARS:
                continue
            reasons.append(f"completed {b.year_built} against your "
                           f"{asset.year_built} (within {AGE_BAND_YEARS} years)")
        matches.append(PeerMatch(b, tuple(reasons)))

    if len(matches) < MIN_PEERS:
        return Refusal(
            f"only {len(matches)} qualifying peer(s) for {asset.name} in "
            f"{asset.submarket}; {MIN_PEERS} are required before a comparison "
            f"is more than an anecdote")
    return matches


def compare(asset: Asset, store: Store) -> Comparison | Refusal:
    """Assemble the two benchmarks and the gap. Refusals flow through."""
    peers = peer_set(asset, store)
    if isinstance(peers, Refusal):
        return peers

    cmp = Comparison(
        asset_name=asset.name,
        submarket=asset.submarket,
        peers=peers,
        age_rule_applied=asset.year_built is not None,
    )

    # Benchmark 1: the peer street's valuation level -- the median across
    # per-building figures, each building one vote (the area-weighting across
    # a building's hereditaments happened at harvest, inside its fact). E-4
    # discipline on every level: the test is `f is not None and f.value is
    # not None`, never bare truth.
    vals: list[float] = []
    for pm in peers:
        f = store.get(VALUATION_METRIC, pm.building.submarket,
                      building=pm.building.name)
        v_psm = None
        if f is not None and f.value is not None:
            v_psm = f.value
            vals.append(v_psm)
            cmp.evidence.append(f)
            cmp.valuation_period = str(f.period)
        cmp.rows.append({
            "peer": pm.building.name,
            "sqft": pm.building.sqft,
            "year_built": pm.building.year_built,
            "epc": pm.building.epc_rating,
            "valuation_psm": None if v_psm is None else round(v_psm, 2),
            "reported_rent_psf": None,
            "reasons": list(pm.reasons),
        })

    if len(vals) >= MIN_VALUATIONS:
        cmp.valuation_avg_psm = median(vals)
        cmp.valuation_n = len(vals)
    else:
        cmp.valuation_refusal = (
            f"only {len(vals)} of {len(peers)} peers carry a published "
            f"valuation; {MIN_VALUATIONS} are required for a median")

    # Benchmark 2: achieved rents from reported lettings inside the peer set.
    # Event building strings must equal roster names -- a harvest-time
    # discipline, asserted by test_roster_and_events_share_names.
    by_name = {pm.building.name: pm for pm in peers}
    rents: list[float] = []
    for e in store.find_events(type="letting", limit=len(store.events)):
        b, rent = e.get("building"), e.get("rent_psf")
        if b in by_name and rent is not None:
            rents.append(float(rent))
            for row in cmp.rows:
                if row["peer"] == b:
                    row["reported_rent_psf"] = float(rent)
    if len(rents) >= MIN_LETTINGS:
        cmp.achieved_psf = median(rents)
        cmp.achieved_n = len(rents)
    else:
        cmp.achieved_refusal = (
            f"{len(rents)} reported letting(s) with a rent inside the peer "
            f"set; {MIN_LETTINGS} are required to benchmark achieved rents")

    # The gaps, like for like only (C-3). Valuation against valuation: the
    # asset's own £/m² against the peer street's, both on the same fixed
    # valuation date. psm_to_psf converts the £/m² gap to £/ft² so the annual
    # figure is that gap carried across the asset's square footage.
    #
    # The asset's own figure prefers the store: a real holding sits in the
    # same VOA list as its peers, so both sides of the gap carry the same
    # source and date. The yaml field is the fallback for a figure the user
    # supplied themselves, and stays labelled as theirs wherever it renders.
    own = store.get(VALUATION_METRIC, asset.submarket, building=asset.name)
    if own is not None and own.value is not None:
        cmp.asset_value_psm = own.value
        cmp.asset_value_from_store = True
        cmp.evidence.append(own)
    else:
        cmp.asset_value_psm = asset.rateable_value_psm
    if cmp.asset_value_psm is not None and cmp.valuation_avg_psm is not None:
        gap_psm = cmp.asset_value_psm - cmp.valuation_avg_psm
        cmp.valuation_gap_share = gap_psm / cmp.valuation_avg_psm
        cmp.valuation_gap_annual = psm_to_psf(gap_psm) * asset.sqft

    # Contract rent against contract rents.
    if asset.passing_rent_psf is not None and cmp.achieved_psf is not None:
        cmp.passing_vs_achieved_psf = asset.passing_rent_psf - cmp.achieved_psf

    return cmp

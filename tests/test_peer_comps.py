"""Building vs building: the peer set, the like-for-like arithmetic, the card.

Three invariants under test, all learned the hard way:

  C-1  £/m² never renders or compares as psf. The VOA publishes per square
       metre; one constant, one call site, pinned here. Silently ~11x wrong
       otherwise, with no exception to catch it.
  C-2  A thin peer set refuses; it never averages. Two buildings are an
       anecdote wearing a median's clothes.
  C-3  Comparisons run like for like, never across bases. VOA valuations sit
       systematically below headline rents (~£270/m² against ~£50 psf
       passing), so valuation compares with valuation and contract rent with
       contract rents. A passing rent minus a valuation median is a labelled
       number and still a misleading one.

Run:  uv run python tests/test_peer_comps.py     (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent import comps
from cre_agent.comps import (MIN_PEERS, PSF_PER_PSM, Comparison, Refusal,
                             compare, peer_set, psm_to_psf)
from cre_agent.signals import ACTIONS, _peer_verdict, detect_all, peer_gap
from cre_agent.store import Building, Fact, Period, Source, Store
from cre_agent.watchlist import Asset, SubmarketIndex, Watchlist

SRC = Source(publisher="T", title="fixture", published="2026-01-01", url="x")
CW = "Canary Wharf"


def bld(name, sqft=200_000, year=2001, **kw) -> Building:
    return Building(name=name, submarket=CW, source=SRC, sqft=sqft,
                    year_built=year, **kw)


def rv(building, value) -> Fact:
    return Fact(metric="rateable_value_avg", submarket=CW,
                period=Period.parse("2024"), value=value, unit="gbp_psm",
                source=SRC, building=building)


def fixture_store(buildings, facts=(), events=()) -> Store:
    return Store(list(facts), list(events), [SRC], SubmarketIndex.load(),
                 buildings=list(buildings))


def asset(**kw) -> Asset:
    base = dict(name="Test Tower", submarket=CW, sqft=200_000, year_built=2001,
                passing_rent_psf=50.0, rateable_value_psm=280.0)
    return Asset(**(base | kw))


# -- C-1: the conversion constant -------------------------------------------

def test_psm_to_psf_constant_is_pinned():
    """1 ft = 0.3048 m exactly, so 1 ft² = 0.09290304 m² exactly."""
    assert PSF_PER_PSM == 0.09290304
    assert abs(psm_to_psf(400.0) - 37.161216) < 1e-9


def test_store_renders_psm_as_psm_never_psf():
    f = rv("X", 270.0)
    assert "m²" in f.render_value() and "psf" not in f.render_value()


# -- the matcher ------------------------------------------------------------

def test_size_band_is_two_times_either_way():
    s = fixture_store([bld("A", 400_000), bld("B", 400_001), bld("C", 100_000),
                       bld("D", 99_999), bld("E", 200_000)])
    got = peer_set(asset(), s)
    assert [p.building.name for p in got] == ["A", "C", "E"]


def test_age_band_is_ten_years_either_way():
    s = fixture_store([bld("A", year=2011), bld("B", year=2012),
                       bld("C", year=1991), bld("D", year=1990),
                       bld("E", year=2001)])
    got = peer_set(asset(), s)
    assert [p.building.name for p in got] == ["A", "C", "E"]


def test_undated_asset_skips_age_rule_and_says_so():
    """A rule whose input is missing on the asset is a filter on nothing --
    skipping silently is the supply_shock defect. It skips loudly instead."""
    s = fixture_store([bld("A", year=1950), bld("B", year=None), bld("C")])
    got = peer_set(asset(year_built=None), s)
    assert {p.building.name for p in got} == {"A", "B", "C"}
    c = compare(asset(year_built=None), s)
    assert c.age_rule_applied is False


def test_dated_asset_excludes_undated_candidates():
    """A peer that cannot show its age has no place in an age-banded set."""
    s = fixture_store([bld("A"), bld("B", year=None), bld("C"), bld("D")])
    assert {p.building.name for p in peer_set(asset(), s)} == {"A", "C", "D"}


def test_every_match_names_its_reasons():
    got = peer_set(asset(), fixture_store([bld("A"), bld("B"), bld("C")]))
    for pm in got:
        assert pm.reasons and any("sq ft" in r for r in pm.reasons)
        assert any("submarket" in r for r in pm.reasons)


# -- C-2: refusals ----------------------------------------------------------

def test_thin_peer_set_refuses_not_averages():
    r = peer_set(asset(), fixture_store([bld("A"), bld("B")]))
    assert isinstance(r, Refusal) and str(MIN_PEERS) in r.reason


def test_no_roster_refuses_with_the_reason():
    r = peer_set(asset(submarket="Mayfair"), fixture_store([bld("A")]))
    assert isinstance(r, Refusal) and "Canary Wharf" in r.reason


def test_asset_without_sqft_refuses():
    r = peer_set(asset(sqft=None), fixture_store([bld("A"), bld("B"), bld("C")]))
    assert isinstance(r, Refusal) and "floor area" in r.reason


def test_valuation_median_needs_three_published_levels():
    blds = [bld(n) for n in "ABCD"]
    s = fixture_store(blds, facts=[rv("A", 260.0), rv("B", 280.0)])
    c = compare(asset(), s)
    assert c.valuation_avg_psm is None and "2 of 4" in c.valuation_refusal
    sev, reason, action = _peer_verdict(asset(), c)
    assert action == "hold" and "no verdict" in reason


def test_achieved_median_needs_two_reported_rents():
    blds = [bld(n) for n in "ABC"]
    facts = [rv(n, 270.0) for n in "ABC"]
    one = [{"type": "letting", "building": "A", "rent_psf": 55.0, "date": "2026Q2",
            "_source": SRC}]
    c = compare(asset(), fixture_store(blds, facts, one))
    assert c.achieved_psf is None and "2 are required" in c.achieved_refusal
    two = one + [{"type": "letting", "building": "B", "rent_psf": 45.0,
                  "date": "2026Q2", "_source": SRC}]
    c = compare(asset(), fixture_store(blds, facts, two))
    assert c.achieved_psf == 50.0 and c.passing_vs_achieved_psf == 0.0


# -- C-3: like for like, and the verdict bands ------------------------------

def _store_at(street: float) -> Store:
    return fixture_store([bld(n) for n in "ABC"], [rv(n, street) for n in "ABC"])


def test_verdict_above_the_band_is_risk_regear():
    c = compare(asset(rateable_value_psm=280.0 * 1.06), _store_at(280.0))
    sev, reason, action = _peer_verdict(asset(), c)
    assert (sev, action) == ("RISK", "regear")


def test_verdict_below_the_band_is_opportunity_reprice():
    c = compare(asset(rateable_value_psm=280.0 * 0.94), _store_at(280.0))
    assert (_peer_verdict(asset(), c)[0], _peer_verdict(asset(), c)[2]) \
        == ("OPPORTUNITY", "re-price")


def test_verdict_inside_the_band_is_watch_hold():
    c = compare(asset(rateable_value_psm=280.0 * 1.02), _store_at(280.0))
    sev, reason, action = _peer_verdict(asset(), c)
    assert (sev, action) == ("WATCH", "hold")


def test_no_cross_basis_figure_exists_anywhere():
    """The defect this design retired: passing rent minus valuation median.
    The Comparison holds no such field and the verdict never renders one."""
    assert not hasattr(Comparison("x", CW, [], True), "gap_psf")
    c = compare(asset(), _store_at(270.0))
    _, reason, _ = _peer_verdict(asset(), c)
    assert "like for like" in reason


def test_asset_without_valuation_holds_rather_than_borrowing_passing():
    c = compare(asset(rateable_value_psm=None), _store_at(270.0))
    assert c.valuation_gap_share is None
    sev, reason, action = _peer_verdict(asset(rateable_value_psm=None), c)
    assert action == "hold" and "valuation" in reason


# -- the store's building axis ----------------------------------------------

def test_market_lookup_never_returns_a_building_fact():
    s = Store.load()
    assert s.get("rateable_value_avg", CW) is None
    assert s.get("rateable_value_avg", CW,
                 building="8 Canada Square").value == 233.47


def test_two_buildings_same_metric_both_survive_dedupe():
    s = Store.load()
    # 15 Canary Wharf + 32 City Core buildings, holdings included.
    assert len(s.find(metric="rateable_value_avg", building="__any__")) == 47


def test_roster_resolves_downward_like_events():
    s = Store.load()
    assert len(s.find_buildings("Canary Wharf")) == 18
    assert len(s.find_buildings("Docklands")) == 18       # child reached from parent
    assert s.find_buildings("Mayfair") == []


def test_cw_letting_events_with_rents_use_roster_names():
    """The achieved-rent join is an exact name match, so a Canary Wharf
    letting carrying a rent must spell its building as the roster does or the
    figure silently misses the comparison."""
    s = Store.load()
    roster = {b.name for b in s.buildings}
    for e in s.events:
        if e.get("type") == "letting" and e.get("rent_psf") \
                and s.resolve_submarket(e.get("submarket") or "") == "canary_wharf":
            assert e["building"] in roster, e["building"]


# -- the signal on the brief -------------------------------------------------

def test_real_pipeline_city_holdings_get_cards():
    """The shipped brief: peer cards for the two City holdings the roster can
    justify a peer set for, both sides of each card off the same VOA list."""
    s, w = Store.load(), Watchlist.load()
    sigs = {g.id: g for g in detect_all(s, w) if g.id.startswith("peer_gap")}
    assert set(sigs) == {"peer_gap:The Bailey", "peer_gap:108 Cannon Street"}
    for sig in sigs.values():
        name = sig.id.split(":", 1)[1]
        assert sig.match_actions[name] in ACTIONS
        assert "per m²" in sig.headline and "psf" not in sig.headline
        assert "monitor" not in sig.detail
        assert "fictional" not in sig.detail     # nothing here is invented
        assert "(yours)" in sig.detail           # ...but the row is still marked
        assert sig.citations()                   # VOA facts travel as evidence


def test_own_valuation_comes_from_the_store_not_the_yaml():
    """The asset's side of the gap is the same VOA list as the peers' side.
    The Bailey's watchlist entry carries no rateable_value_psm, yet the
    comparison carries its aggregate £616.77/m² -- fetched, with evidence."""
    s, w = Store.load(), Watchlist.load()
    bailey = next(a for a in w.assets if a.name == "The Bailey")
    assert bailey.rateable_value_psm is None
    c = comps.compare(bailey, s)
    assert not isinstance(c, comps.Refusal)
    assert c.asset_value_from_store is True
    assert c.asset_value_psm == 616.77
    own = [f for f in c.evidence if f.building == "The Bailey"]
    assert own and own[0].source.publisher == "Valuation Office Agency"


def test_a_holding_is_never_its_own_peer():
    """The holdings sit on the roster now, so self-exclusion carries load."""
    s, w = Store.load(), Watchlist.load()
    for a in w.assets:
        c = comps.compare(a, s)
        if isinstance(c, comps.Refusal):
            continue
        assert a.name not in [pm.building.name for pm in c.peers]


def test_refusal_emits_no_signal():
    """Three holdings stay silent, each for a computed reason: 138 Cheapside
    is 1958 stock the age band finds no street for, 99 City Road has no City
    Fringe roster, Regent Quarter has no King's Cross roster. A comparison
    that cannot name its peers does not fire."""
    s, w = Store.load(), Watchlist.load()
    fired = {g.id for g in peer_gap(s, w)}
    for name in ("138 Cheapside", "99 City Road", "Regent Quarter"):
        assert f"peer_gap:{name}" not in fired
    cheapside = next(a for a in w.assets if a.name == "138 Cheapside")
    r = comps.compare(cheapside, s)
    assert isinstance(r, comps.Refusal) and "qualifying peer" in r.reason


def test_empty_watchlist_stays_fully_functional():
    s = Store.load()
    empty = Watchlist([], SubmarketIndex.load())
    assert not [g for g in detect_all(s, empty) if g.id.startswith("peer_gap")]
    assert detect_all(s, empty)               # the market-wide brief still fires


# -- the chat tool shares the brief's code path ------------------------------

def test_compare_building_tool_matches_the_brief():
    from cre_agent.llm.gemini import Agent
    s, w = Store.load(), Watchlist.load()
    agent = Agent(s, w, api_key=None)         # tools work without a key
    out = agent._run_tool("compare_building", {"asset": "The Bailey"})
    assert out["found"]
    assert "VOA" in out["asset_value_source"]     # not user-supplied, not absent
    assert out["recommended_action"] in ACTIONS
    sig = [g for g in detect_all(s, w) if g.id == "peer_gap:The Bailey"][0]
    assert out["verdict"] == sig.match_reasons["The Bailey"]
    assert out["recommended_action"] == sig.match_actions["The Bailey"]


def test_compare_building_tool_relays_refusals():
    from cre_agent.llm.gemini import Agent
    agent = Agent(Store.load(), Watchlist.load(), api_key=None)
    # A real holding with no roster: the refusal names what rosters DO exist.
    out = agent._run_tool("compare_building", {"asset": "99 City Road"})
    assert out["found"] is False
    assert "City Fringe" in out["refusal"]
    assert "Canary Wharf and City Core" in out["refusal"]
    out = agent._run_tool("compare_building", {"asset": "Regent Quarter"})
    assert out["found"] is False and "King's Cross" in out["refusal"]
    out = agent._run_tool("compare_building", {"asset": "No Such House"})
    assert out["found"] is False and "watchlist" in out["message"]


# -- coverage owns the ninth area -------------------------------------------

def test_coverage_area_nine_is_wired_and_firing():
    from cre_agent.coverage import assess
    row = [r for r in assess(Store.load(), Watchlist.load())
           if r.area.n == 9][0]
    assert row.firing and row.surfaced_by == "peer_gap"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

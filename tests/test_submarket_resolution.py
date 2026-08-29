"""Submarket names resolve through the vocabulary, not by spelling.

Every test here failed before the fix. Two defects, one cause: code compared
submarket strings directly instead of consulting SubmarketIndex.

  Goal 2  "What is vacancy in Mayfair?" answered "I don't have that" about a
          figure sitting in the store under "West End Core (Mayfair/St
          James's)".
  Goal 1  "Which occupiers signed in the West End?" silently dropped
          Anthropic's 158,138 sq ft, the largest letting in the report,
          because it is filed under "North of Oxford Street East" -- a child
          of the West End that submarkets.yaml has always declared.

The two resolve in opposite directions and the asymmetry is the point:
facts climb UP to the nearest published geography, events match DOWN into
everything the queried submarket contains.

Run:  uv run python tests/test_submarket_resolution.py     (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.llm.gemini import Agent
from cre_agent.store import Store
from cre_agent.watchlist import Watchlist


def store() -> Store:
    return Store.load()


def tools() -> Agent:
    """The agent's tool surface. No API key needed: _run_tool never calls out."""
    return Agent(Store.load(), Watchlist.load(), api_key=None)


# -- facts resolve upward ---------------------------------------------------

def test_alias_finds_the_fact_it_names():
    s = store()
    for alias in ("Mayfair", "St James's", "West End Core"):
        f = s.get("vacancy_rate", alias)
        assert f is not None, f"{alias} returned nothing"
        assert f.value == 4.4
        assert f.submarket == "West End Core (Mayfair/St James's)"


def test_alias_resolves_for_a_child_submarket():
    """Clerkenwell and Shoreditch are City Fringe in submarkets.yaml."""
    s = store()
    for alias in ("Clerkenwell", "Shoreditch", "City Northern Fringe"):
        assert s.get("vacancy_rate", alias).value == 8.0


def test_exact_node_wins_over_the_parent():
    """A West End question must not be answered with the West End Core figure."""
    s = store()
    we = s.get("vacancy_rate", "West End")
    assert we is not None and we.submarket == "West End"
    assert we.value is None                      # level not published (E-4)
    assert we.delta("vs_avg").render() == "+190 bps"


def test_climb_is_off_by_default():
    """Detectors ask about a submarket and mean that submarket.

    Southbank holds no facts (Canary Wharf, the old example here, now
    publishes via the Colliers seed). Answering with the Central London
    figure unasked would attribute a number to a geography the source never
    published it for.
    """
    s = store()
    assert s.get("vacancy_rate", "Southbank") is None
    climbed = s.get("vacancy_rate", "Southbank", climb=True)
    assert climbed is not None and climbed.submarket == "Central London"
    # And the geography that used to be the gap now answers at its own node.
    assert s.get("vacancy_rate", "Canary Wharf").submarket == "Canary Wharf"


def test_unknown_submarket_never_climbs():
    s = store()
    assert s.get("vacancy_rate", "Basingstoke", climb=True) is None
    assert s.resolve_submarket("Basingstoke") is None
    # "Canary Wharf" used to be an alias of docklands; the peer-comps work
    # gave the estate its own node because Colliers publishes figures for the
    # estate, not for all of E14. The hierarchy still connects them.
    assert s.resolve_submarket("Canary Wharf") == "canary_wharf"
    assert s.index.covers("Canary Wharf", "Docklands")
    assert s.index.covers("Canary Wharf", "Central London")


def test_detector_output_is_unchanged_by_resolution():
    """The shipped brief must be byte-identical. climb defaults off for this."""
    from cre_agent.signals import detect_all
    sigs = detect_all(Store.load(), Watchlist.load())
    assert [s.id for s in sigs] == [
        "peer_gap:Meridian Quay Tower",       # the peer-comps lead card
        "quality_spread:City",
        "quality_spread:West End",
        "supply_shock:Central London",
        "large_occupier_squeeze:Central London",
        "sector_demand:Insurance & Financial",
    ]


# -- events resolve downward ------------------------------------------------

def test_west_end_query_reaches_anthropic():
    """The regression that started this: the report's largest letting.

    1 Triton Square is North of Oxford Street East, a declared child of the
    West End. An exact string compare dropped it from its own submarket.
    """
    hits = store().find_events(submarket="West End")
    names = [e.get("occupier") for e in hits]
    assert "Anthropic" in names
    assert max(e.get("sqft") or 0 for e in hits) == 158138
    # investments in this submarket carry no floor area; the filter
    # must not assume every event has one
    assert any(e.get("sqft") is None for e in hits)


def test_events_roll_up_to_central_london():
    s = store()
    everything = s.find_events(submarket="Central London")
    assert len(everything) == len(s.events) - s.events_missing_submarket()


def test_event_without_a_submarket_never_matches_a_filter():
    s = store()
    assert s.events_missing_submarket() == 13
    for e in s.find_events(submarket="Central London"):
        assert e.get("submarket")


# -- the agent's tool surface -----------------------------------------------

def test_tool_distinguishes_a_typo_from_a_data_gap():
    """A refusal is only a correct answer when the data genuinely is absent."""
    a = tools()
    typo = a._run_tool("get_metric",
                       {"metric": "vacancy_rate", "submarket": "Basingstoke"})
    assert typo["found"] is False and "not a submarket" in typo["message"]

    gap = a._run_tool("get_metric",
                      {"metric": "grade_b_rent_avg", "submarket": "Southbank"})
    assert gap["found"] is False
    assert "recognised submarket" in gap["message"]
    assert "publishes no" in gap["message"]


def test_tool_discloses_a_broader_geography():
    a = tools()
    r = a._run_tool("get_metric",
                    {"metric": "vacancy_rate", "submarket": "Southbank"})
    assert r["found"] is True
    assert r["asked_about"] == "Southbank"
    assert r["submarket"] == "Central London"
    assert "does not break out Southbank" in r["broader_geography"]


def test_tool_does_not_cry_broader_for_an_exact_alias():
    a = tools()
    r = a._run_tool("get_metric",
                    {"metric": "vacancy_rate", "submarket": "Mayfair"})
    assert r["found"] is True and "broader_geography" not in r


def test_filtered_events_state_what_the_filter_cannot_see():
    a = tools()
    r = a._run_tool("find_market_activity", {"submarket": "West End"})
    assert r["count"] >= 1
    assert "13 of 20" in r["coverage_caveat"]

    unfiltered = a._run_tool("find_market_activity", {})
    assert "coverage_caveat" not in unfiltered


# -- sectors are reachable, not just advertised -----------------------------

def test_list_available_only_advertises_fetchable_sectors():
    """Advertising a sector get_metric cannot fetch is worse than silence:
    the model asks, receives the undifferentiated total, and reports it as
    the sector figure."""
    a = tools()
    advertised = a._run_tool("list_available", {})["sectors"]
    assert len(advertised) == 9 - 2   # 7 distinct names across 9 sector facts
    for sector in advertised:
        hits = [f for f in a.store.facts if f.sector == sector]
        assert hits, f"{sector} advertised but holds no facts"


def test_sector_slice_is_not_the_total():
    a = tools()
    total = a._run_tool("get_metric",
                        {"metric": "active_demand", "submarket": "Central London"})
    tech = a._run_tool("get_metric",
                       {"metric": "active_demand", "submarket": "Central London",
                        "sector": "Tech & Media"})
    assert total["value"] == "15,700,000 sq ft"
    assert total["sector"] == "all sectors"
    assert tech["value"] == "3,100,000 sq ft"
    assert tech["sector"] == "Tech & Media"


def test_omitting_sector_still_means_the_total():
    """E-3: active_demand must never silently return a slice."""
    s = store()
    assert s.get("active_demand", "Central London").sector is None


def test_get_metric_declares_sector():
    from cre_agent.llm.gemini import _declarations
    decl = {d.name: set((d.parameters.properties or {}).keys())
            for d in _declarations()}
    assert "sector" in decl["get_metric"]


def test_events_tool_exposes_submarket():
    from cre_agent.llm.gemini import _declarations
    decl = {d.name: set((d.parameters.properties or {}).keys())
            for d in _declarations()}
    assert "submarket" in decl["find_market_activity"]


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

"""The join: a market fact attached to a building the user actually holds.

Before this, `supply_shock` called `_match(watchlist, submarket=None)` -- a
filter on nothing -- so the brief claimed all three assets were exposed to a
record completion year without being able to name a reason for any of them. An
unjustified exposure is the same class of defect as an unsourced figure.

The window comes from `pipeline_to_2029`, period `2026H2-2029`: a range the
source published, rather than a tolerance invented around a point estimate.

Run:  uv run python tests/test_watchlist_join.py     (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.signals import (ACTIONS, _pipeline_window, _reversion, detect_all,
                               quality_spread, supply_shock)
from cre_agent.store import Store
from cre_agent.watchlist import Asset, SubmarketIndex, Watchlist, parse_ym

WINDOW = ("2026-07", "2029-12")


def wl(*assets: Asset) -> Watchlist:
    return Watchlist(list(assets), SubmarketIndex.load(), "test")


def asset(**kw) -> Asset:
    base = dict(name="X", submarket="City Fringe", grade="B", sqft=10_000)
    return Asset(**(base | kw))


# -- the window is read off the published period ----------------------------

def test_window_opens_in_july_not_january():
    """store's _RANGE drops the H2, so Period alone opens this window in Q1."""
    f = Store.load().get("pipeline_to_2029", "Central London")
    assert f.period.raw == "2026H2-2029"
    assert _pipeline_window(f) == WINDOW


def test_period_object_would_have_got_this_wrong():
    from cre_agent.store import Period
    p = Period.parse("2026H2-2029")
    assert p.months == (1, 12)                        # the H2 is gone
    assert p.contains(Period.parse("2026Q1")) is True  # ... so Q1 answers True


# -- lease-window matching --------------------------------------------------

def test_break_inside_the_window_matches():
    assert [a.name for a in wl(asset(break_date="2027-09")).matching(
        lease_event_between=WINDOW)] == ["X"]


def test_break_wins_over_expiry():
    a = asset(break_date="2027-09", lease_expiry="2035-01")
    assert a.lease_event() == "2027-09"
    assert wl(a).matching(lease_event_between=WINDOW)


def test_no_break_falls_back_to_expiry():
    a = asset(lease_expiry="2028-03")
    assert a.lease_event() == "2028-03"
    assert wl(a).matching(lease_event_between=WINDOW)


def test_neither_break_nor_expiry_does_not_raise():
    assert wl(asset()).matching(lease_event_between=WINDOW) == []


def test_event_outside_the_window_is_excluded():
    assert wl(asset(lease_expiry="2031-06")).matching(lease_event_between=WINDOW) == []


def test_inside_the_calendar_year_but_before_the_window_opens():
    """2026-03 is in 2026 and still outside a window that opens in July."""
    assert wl(asset(break_date="2026-03")).matching(lease_event_between=WINDOW) == []
    assert wl(asset(break_date="2026-07")).matching(lease_event_between=WINDOW)


def test_malformed_date_fails_loud():
    for bad in ("2027", "2027-13", "Sept 2027", None):
        try:
            parse_ym(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def test_empty_watchlist_matches_nothing_and_does_not_raise():
    assert wl().matching(lease_event_between=WINDOW) == []


# -- F3: the defect this fixes ----------------------------------------------

def test_supply_shock_no_longer_claims_every_asset():
    s = supply_shock(Store.load(), Watchlist.load())[0]
    # Meridian Quay Tower joined the shipped watchlist with a 2027-12 break,
    # inside the 2026H2-2029 window, so its claim is justified.
    assert set(s.affected) == {"Mayfair House", "Clerkenwell Works",
                               "Meridian Quay Tower"}
    assert "120 Fenchurch Street" not in s.affected   # expiry 2031-06, outside


def test_every_match_names_a_reason_and_an_action():
    for sig in detect_all(Store.load(), Watchlist.load()):
        for name in sig.affected:
            assert sig.match_reasons.get(name), f"{sig.id}/{name} has no reason"
            assert sig.match_actions.get(name) in ACTIONS, f"{sig.id}/{name}"


def test_monitor_is_not_a_permitted_action():
    assert "monitor" not in ACTIONS
    from cre_agent.llm.gemini import SYSTEM_PROMPT
    assert 'Never close with "monitor"' in SYSTEM_PROMPT
    for sig in detect_all(Store.load(), Watchlist.load()):
        for text in list(sig.match_reasons.values()) + [sig.detail]:
            assert "monitor" not in text.lower()


# -- the E-4 trap -----------------------------------------------------------

def test_unpublished_level_states_the_gap_instead_of_asserting_a_figure():
    """West End grade_b_rent_avg is a truthy Fact whose value is None."""
    store = Store.load()
    b = store.get("grade_b_rent_avg", "West End")
    assert b is not None and b.value is None      # truthy, but has no level
    sig = [s for s in quality_spread(store, Watchlist.load())
           if s.id == "quality_spread:West End"][0]
    reason = sig.match_reasons["Mayfair House"]
    assert "not the level" in reason
    assert "against the West End Grade B average" not in reason


def test_published_level_computes_the_reversion():
    store = Store.load()
    sig = [s for s in quality_spread(store, Watchlist.load())
           if s.id == "quality_spread:City"][0]
    reason = sig.match_reasons["Clerkenwell Works"]
    assert "£240,920" in reason                   # (52.00 - 45.66) * 38,000
    assert sig.match_actions["Clerkenwell Works"] == "regear"


def test_the_two_figures_are_never_presented_as_summable():
    """(passing - B) and (A - passing) sum to the grade gap. Render two, not three."""
    store = Store.load()
    sig = [s for s in quality_spread(store, Watchlist.load())
           if s.id == "quality_spread:City"][0]
    reason = sig.match_reasons["Clerkenwell Works"]
    assert "£240,920" in reason and "£919,980" in reason
    assert "1,160,900" not in reason              # the sum is never rendered


def test_epc_below_c_defers_capex():
    store = Store.load()
    grade_a = store.get("grade_a_rent_avg", "West End")
    grade_b = store.get("grade_b_rent_avg", "West End")
    ready = asset(passing_rent_psf=82.0, sqft=24_000, epc_rating="C")
    unready = asset(passing_rent_psf=82.0, sqft=24_000, epc_rating="D")
    assert _reversion(ready, grade_a, grade_b)[1] == "refurbish"
    assert _reversion(unready, grade_a, grade_b)[1] == "defer capex"


# -- the base case stays functional -----------------------------------------

def test_market_wide_brief_is_complete_with_no_watchlist():
    store = Store.load()
    empty = detect_all(store, wl())
    # One fewer than the full brief: peer_gap compares a HOLDING to its
    # street, so with no holdings there is honestly nothing to compare --
    # unlike the market signals, which must all still fire.
    assert len(empty) == len(detect_all(store, Watchlist.load())) - 1
    assert not [s for s in empty if s.id.startswith("peer_gap")]
    assert all(s.affected == [] for s in empty)
    assert all(s.detail and s.citations() for s in empty)


def test_large_occupier_squeeze_still_names_its_asset():
    """An unset scope would silently delete a correct pre-existing match."""
    sigs = {s.id: s for s in detect_all(Store.load(), Watchlist.load())}
    assert sigs["large_occupier_squeeze:Central London"].affected == [
        "120 Fenchurch Street", "Meridian Quay Tower"]


def test_two_consecutive_runs_are_identical():
    def run():
        return [(s.id, s.severity, s.headline, s.detail, tuple(s.affected),
                 tuple(sorted(s.match_reasons.items())),
                 tuple(sorted(s.match_actions.items())))
                for s in detect_all(Store.load(), Watchlist.load())]
    assert run() == run()


# -- the coverage table claims nothing it cannot resolve --------------------

def test_every_declared_detector_exists():
    """Rename a detector and its area must drop, not keep claiming coverage."""
    from cre_agent.coverage import AREAS
    from cre_agent.signals import DETECTORS
    registered = {d.__name__ for d in DETECTORS}
    for area in AREAS:
        if area.detector:
            assert area.detector in registered, f"area {area.n}: {area.detector}"


def test_every_declared_metric_exists_or_the_area_reads_as_a_gap():
    from cre_agent.coverage import AREAS, GAP, assess
    have = set(Store.load().metrics())
    for row in assess(Store.load(), Watchlist.load()):
        if row.status != GAP:
            assert all(m in have for m in row.area.metrics), row.area.name
    assert any(a.metrics and set(a.metrics) - have for a in AREAS) is False


def test_macro_reports_as_a_gap_rather_than_a_guess():
    from cre_agent.coverage import GAP, assess
    macro = [r for r in assess(Store.load(), Watchlist.load()) if r.area.n == 6][0]
    assert macro.status == GAP and macro.surfaced_by == "not surfaced"


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

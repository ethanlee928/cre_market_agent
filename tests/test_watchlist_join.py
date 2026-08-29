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
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.signals import (ACTIONS, _pipeline_window, _reversion, detect_all,
                               quality_spread, supply_shock)
from cre_agent.store import Store
from cre_agent.watchlist import (Asset, SubmarketIndex, Watchlist,
                                 WatchlistSchemaError, parse_ym)

WINDOW = ("2026-07", "2029-12")


def wl(*assets: Asset) -> Watchlist:
    return Watchlist(list(assets), SubmarketIndex.load(), "test")


def asset(**kw) -> Asset:
    base = dict(name="X", submarket="City Fringe", grade="B", sqft=10_000)
    return Asset(**(base | kw))


# -- a user typo names itself ------------------------------------------------
# config/watchlist.yaml is the one file the README asks a non-technical user
# to edit, and app.py loads it at module scope, so a bare TypeError out of
# Asset(**row) took the whole brief down behind a traceback naming no file,
# no line and no fix.

def _yaml(body: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "watchlist.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def _refused(body: str) -> str:
    try:
        Watchlist.load(_yaml(body))
    except WatchlistSchemaError as e:
        return str(e)
    raise AssertionError("expected a WatchlistSchemaError")


def test_unknown_field_names_itself_and_the_valid_ones():
    msg = _refused("""
        assets:
          - {name: Some House, submarket: City, passing_rent: 62.5}
    """)
    assert "passing_rent" in msg, "the message must name the offending key"
    assert "passing_rent_psf" in msg, "and the field the user meant"
    assert "watchlist.yaml" in msg and "Some House" in msg


def test_missing_required_field_names_itself():
    msg = _refused("""
        assets:
          - {submarket: City, sqft: 100000}
    """)
    assert "name" in msg and "missing" in msg.lower()


def test_a_bare_string_asset_is_refused_not_splatted():
    msg = _refused("""
        assets:
          - Some House
    """)
    assert "not a mapping" in msg


def test_a_typo_never_degrades_to_a_silently_shorter_portfolio():
    """Fail loud, not partial: a half-loaded watchlist is a holding missing
    from the brief with nothing on screen saying so."""
    try:
        Watchlist.load(_yaml("""
            assets:
              - {name: Good House, submarket: City}
              - {name: Bad House, submarket: City, passing_rent: 1}
        """))
    except WatchlistSchemaError as e:
        assert "Bad House" in str(e)
    else:
        raise AssertionError("one bad row must not load as a one-asset portfolio")


def test_a_valid_watchlist_still_loads():
    w = Watchlist.load(_yaml("""
        label: Mine
        assets:
          - {name: Some House, submarket: City, sqft: 100000, passing_rent_psf: 62.5}
    """))
    assert len(w) == 1 and w.label == "Mine"
    assert w.assets[0].passing_rent_psf == 62.5


def test_an_absent_or_empty_file_is_still_the_empty_watchlist():
    """The zero-state is a hard requirement; validation must not break it."""
    assert len(Watchlist.load(Path(tempfile.mkdtemp()) / "nope.yaml")) == 0
    assert len(Watchlist.load(_yaml(""))) == 0
    assert len(Watchlist.load(_yaml("label: Mine\nassets:\n"))) == 0


def test_the_shipped_watchlist_loads():
    assert len(Watchlist.load()) == 5


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
    # The shipped watchlist is the real Nan Fung portfolio, whose rent roll is
    # not public: no lease events on file, so the delivery-window filter
    # honestly claims nobody. The F3 defect (a filter on nothing claiming
    # everyone) is pinned with synthetic leases: only the asset whose break
    # falls inside 2026H2-2029 may be claimed.
    assert supply_shock(Store.load(), Watchlist.load())[0].affected == []
    inside = asset(name="Inside", break_date="2027-12")
    outside = asset(name="Outside", lease_expiry="2031-06")
    s = supply_shock(Store.load(), wl(inside, outside))[0]
    assert s.affected == ["Inside"]
    assert s.match_reasons["Inside"]


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

# The reversion arithmetic needs a grade-B asset with a passing rent. The
# shipped portfolio is real and carries no rent roll, so these fixtures are
# synthetic by design -- the code path they pin is what lights up the moment
# a user types their own rents into the yaml.

MAYFAIR_B = dict(name="Mayfair House", submarket="Mayfair", grade="B",
                 sqft=24_000, passing_rent_psf=82.0, epc_rating="D")
CLERKENWELL_B = dict(name="Clerkenwell Works", submarket="City Fringe",
                     grade="B", sqft=38_000, passing_rent_psf=52.0,
                     epc_rating="C")


def test_unpublished_level_states_the_gap_instead_of_asserting_a_figure():
    """West End grade_b_rent_avg is a truthy Fact whose value is None."""
    store = Store.load()
    b = store.get("grade_b_rent_avg", "West End")
    assert b is not None and b.value is None      # truthy, but has no level
    sig = [s for s in quality_spread(store, wl(asset(**MAYFAIR_B)))
           if s.id == "quality_spread:West End"][0]
    reason = sig.match_reasons["Mayfair House"]
    assert "not the level" in reason
    assert "against the West End Grade B average" not in reason


def test_published_level_computes_the_reversion():
    store = Store.load()
    sig = [s for s in quality_spread(store, wl(asset(**CLERKENWELL_B)))
           if s.id == "quality_spread:City"][0]
    reason = sig.match_reasons["Clerkenwell Works"]
    assert "£240,920" in reason                   # (52.00 - 45.66) * 38,000
    assert sig.match_actions["Clerkenwell Works"] == "regear"


def test_the_two_figures_are_never_presented_as_summable():
    """(passing - B) and (A - passing) sum to the grade gap. Render two, not three."""
    store = Store.load()
    sig = [s for s in quality_spread(store, wl(asset(**CLERKENWELL_B)))
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
    full = detect_all(store, Watchlist.load())
    # Exactly the peer cards fewer than the full brief: peer_gap compares a
    # HOLDING to its street, so with no holdings there is honestly nothing to
    # compare -- unlike the market signals, which must all still fire.
    peer_cards = [s for s in full if s.id.startswith("peer_gap")]
    assert len(empty) == len(full) - len(peer_cards)
    assert not [s for s in empty if s.id.startswith("peer_gap")]
    assert all(s.affected == [] for s in empty)
    assert all(s.detail and s.citations() for s in empty)


def test_large_occupier_squeeze_still_names_its_asset():
    """An unset scope would silently delete a correct pre-existing match."""
    sigs = {s.id: s for s in detect_all(Store.load(), Watchlist.load())}
    # The three 100,000+ sq ft holdings; 138 Cheapside (80,300) and
    # 108 Cannon Street (38,800) sit below the squeeze's block size.
    assert sigs["large_occupier_squeeze:Central London"].affected == [
        "The Bailey", "99 City Road", "Regent Quarter"]


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

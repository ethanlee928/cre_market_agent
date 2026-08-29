"""Time-axis genericity: the store and detectors must not pin to a year.

Every test here failed before the fix. The bugs only surface once a second
seed file is merged, which is exactly the thing that happens every quarter,
so none of them would have shown up in a single-quarter demo.

Run:  uv run python tests/test_time_axis.py     (or pytest, if installed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.signals import (detect_all, large_occupier_squeeze,
                               quality_spread, supply_shock)
from cre_agent.store import Delta, Fact, Period, SeedSchemaError, Source, Store
from cre_agent.watchlist import Watchlist

SRC = Source("Test", "Fixture", "2027-01-01", "https://example.invalid")


def fact(metric, period, value, submarket="City", unit="count", **kw) -> Fact:
    return Fact(metric=metric, submarket=submarket, period=Period.parse(period),
                value=value, unit=unit, source=SRC, **kw)


def seed(path: Path, published: str, facts: list[dict]) -> Path:
    path.write_text(json.dumps({
        "source": {"publisher": "Test", "title": f"Fixture {published}",
                   "published": published, "url": "https://example.invalid"},
        "facts": facts,
    }))
    return path


# --------------------------------------------------------------------------
# Period ordering
# --------------------------------------------------------------------------

def test_period_orders_chronologically():
    """order=True sorted on `kind` as a string: "half" < "quarter" < "year"."""
    P = Period.parse
    assert P("2026H2") > P("2026Q1"), "Jul-Dec must rank after Jan-Mar"
    assert P("2027H1") > P("2026Q4"), "next year must rank after last quarter"
    assert P("2026Q4") > P("2026H1")
    assert P("2026") > P("2026Q1"), "the full year closes after Q1"
    assert max([P("2026Q2"), P("2027H1"), P("2026")]) == P("2027H1")


# --------------------------------------------------------------------------
# get(): recency vs grain
# --------------------------------------------------------------------------

def test_latest_wins_over_tighter_grain():
    """The silent-wrong-number bug. Grain used to outrank recency."""
    store = Store([fact("take_up", "2026Q2", 100.0),
                   fact("take_up", "2027H1", 999.0)], [], [SRC])
    got = store.get("take_up", "City")
    assert str(got.period) == "2027H1", f"got {got.period} value {got.value}"
    assert got.value == 999.0


def test_explicit_period_still_prefers_tightest():
    """E-2 must survive the fix: asking for Q2 gets Q2, not the enclosing H1."""
    store = Store([fact("take_up", "2026Q2", 10.0),
                   fact("take_up", "2026H1", 20.0)], [], [SRC])
    assert store.get("take_up", "City", "2026Q2").value == 10.0


def test_explicit_period_falls_back_to_enclosing():
    """E-2: a Q2 miss still resolves to the H1 that contains it."""
    store = Store([fact("take_up", "2026H1", 20.0)], [], [SRC])
    assert store.get("take_up", "City", "2026Q2").value == 20.0


def test_multi_quarter_load_does_not_crash():
    """Dropping next quarter's file into data/ used to raise AmbiguousQuery."""
    with tempfile.TemporaryDirectory() as d:
        rows = lambda v, p: [
            {"metric": "grade_a_rent_avg", "submarket": "City", "period": p,
             "value": v, "unit": "gbp_psf", "yoy_change_pct": 5.0},
            {"metric": "grade_b_rent_avg", "submarket": "City", "period": p,
             "value": v - 30, "unit": "gbp_psf", "yoy_change_pct": -3.0},
        ]
        seed(Path(d) / "seed_2026Q2.json", "2026-08-06", rows(76.0, "2026H1"))
        seed(Path(d) / "seed_2027Q1.json", "2027-02-10", rows(80.0, "2027H1"))
        store = Store.load(sorted(Path(d).glob("seed_*.json")))
        assert store.as_of() == "2027-02-10"
        assert store.get("grade_a_rent_avg", "City").value == 80.0
        assert detect_all(store, Watchlist.load()), "detectors must still fire"


# --------------------------------------------------------------------------
# Year-bearing field names
# --------------------------------------------------------------------------

def test_year_bearing_delta_field_rolls():
    """vs_h1_2025_pct was a literal key; the 2027 report spells it 2026."""
    with tempfile.TemporaryDirectory() as d:
        p = seed(Path(d) / "seed_2027Q2.json", "2027-08-01", [
            {"metric": "grade_a_rent_avg", "submarket": "City", "period": "2027H1",
             "value": 80.0, "unit": "gbp_psf", "vs_h1_2026_pct": 6.0}])
        f = Store.load([p]).get("grade_a_rent_avg", "City")
        delta = f.delta("yoy")
        assert delta is not None, "vs_h1_2026_pct on a 2027 fact means yoy"
        assert delta.render() == "+6.0%"


def test_forecast_year_rolls():
    with tempfile.TemporaryDirectory() as d:
        p = seed(Path(d) / "seed_2027Q2.json", "2027-08-01", [
            {"metric": "prime_rent", "submarket": "City", "period": "2027H1",
             "value": 90.0, "unit": "gbp_psf", "forecast_2028_growth_pct": 4.5}])
        f = Store.load([p]).get("prime_rent", "City")
        assert f.delta("forecast").basis == "2028"


def test_unrelated_year_still_fails_loud():
    """The fix rolls the year; it must not swallow a genuinely new concept."""
    with tempfile.TemporaryDirectory() as d:
        p = seed(Path(d) / "seed_2027Q2.json", "2027-08-01", [
            {"metric": "prime_rent", "submarket": "City", "period": "2027H1",
             "value": 90.0, "unit": "gbp_psf", "vs_h1_2019_pct": 30.0}])
        try:
            Store.load([p])
        except SeedSchemaError as e:
            assert "vs_h1_2019_pct" in str(e)
        else:
            raise AssertionError("an 8-year-back comparison must not parse as yoy")


# --------------------------------------------------------------------------
# Cross-period comparison
# --------------------------------------------------------------------------

def test_get_pair_uses_newest_shared_period():
    """Grade A runs to 2027, Grade B stops at 2026: compare at the shared one."""
    store = Store([
        fact("grade_a_rent_avg", "2026H1", 76.0, unit="gbp_psf"),
        fact("grade_a_rent_avg", "2027H1", 80.0, unit="gbp_psf"),
        fact("grade_b_rent_avg", "2026H1", 45.0, unit="gbp_psf"),
    ], [], [SRC])
    a, b = store.get_pair("grade_a_rent_avg", "grade_b_rent_avg", "City")
    assert a.period == b.period == Period.parse("2026H1")


def test_get_pair_picks_newest_across_mixed_grain():
    """"Newest shared" needs chronological ordering, not field ordering."""
    store = Store([
        fact("grade_a_rent_avg", "2026Q1", 70.0, unit="gbp_psf"),
        fact("grade_a_rent_avg", "2026H2", 76.0, unit="gbp_psf"),
        fact("grade_b_rent_avg", "2026Q1", 44.0, unit="gbp_psf"),
        fact("grade_b_rent_avg", "2026H2", 45.0, unit="gbp_psf"),
    ], [], [SRC])
    a, b = store.get_pair("grade_a_rent_avg", "grade_b_rent_avg", "City")
    assert a.period == Period.parse("2026H2"), f"picked {a.period}, not Jul-Dec"


def test_quality_spread_never_compares_across_years():
    """The invented-number bug.

    Grade A runs to 2027, Grade B stops at 2026. Two independent get() calls
    difference +2.0% (2027 A) against -3.0% (2026 B) and publish a 5.0-point
    spread that exists in no report. The same-period answer is 10.0.
    """
    store = Store([
        fact("grade_a_rent_avg", "2026H1", 76.0, unit="gbp_psf",
             deltas=(Delta("yoy", "prior_year", 7.0, "pct"),)),
        fact("grade_a_rent_avg", "2027H1", 80.0, unit="gbp_psf",
             deltas=(Delta("yoy", "prior_year", 2.0, "pct"),)),
        fact("grade_b_rent_avg", "2026H1", 45.0, unit="gbp_psf",
             deltas=(Delta("yoy", "prior_year", -3.0, "pct"),)),
    ], [], [SRC])
    signals = quality_spread(store, None)
    assert len(signals) == 1, "the City signal must still fire"
    periods = {str(f.period) for f in signals[0].evidence}
    assert periods == {"2026H1"}, f"evidence spans {sorted(periods)}"
    assert "10.0 points" in signals[0].detail, signals[0].detail


def test_large_occupier_squeeze_never_compares_across_periods():
    """Cross-period division silently LOSES a real signal.

    Options are restated at 2027Q1 while requirements stop at 2026Q2. Dividing
    41 requirements by 50 options reads as no imbalance, and the 2.0:1 squeeze
    that the 2026Q2 pair actually shows never reaches the brief.
    """
    store = Store([
        fact("large_grade_a_options", "2026Q2", 21.0, submarket="Central London"),
        fact("large_grade_a_options", "2027Q1", 50.0, submarket="Central London"),
        fact("large_active_requirements", "2026Q2", 41.0, submarket="Central London"),
    ], [], [SRC])
    signals = large_occupier_squeeze(store, None)
    assert len(signals) == 1, "the 2026Q2 squeeze must still be reported"
    assert "2.0:1" in signals[0].headline, signals[0].headline
    periods = {str(f.period) for f in signals[0].evidence}
    assert periods == {"2026Q2"}, f"evidence spans {sorted(periods)}"


# --------------------------------------------------------------------------
# Prose carries the data's year, not a literal
# --------------------------------------------------------------------------

def test_supply_shock_prints_its_own_year():
    """The headline said "in 2026" regardless of what year the fact covered."""
    store = Store([
        fact("completions_forecast", "2028", 6_000_000.0,
             submarket="Central London", unit="sqft",
             extras={"prelet_pct": 30.0}),
    ], [], [SRC])
    signal = supply_shock(store, None)[0]
    assert "2028" in signal.headline, signal.headline
    assert "2026" not in signal.headline, signal.headline
    assert "in 2028" in signal.detail, signal.detail


def test_supply_shock_labels_under_construction_period():
    """Two facts, two measurement dates; the prose must not merge them."""
    store = Store([
        fact("completions_forecast", "2028", 6_000_000.0,
             submarket="Central London", unit="sqft", extras={"prelet_pct": 30.0}),
        fact("under_construction", "2027H1", 14_000_000.0,
             submarket="Central London", unit="sqft", extras={"prelet_pct": 40.0}),
    ], [], [SRC])
    detail = supply_shock(store, None)[0].detail
    assert "at 2027H1" in detail, detail


# --------------------------------------------------------------------------

def test_shipped_seed_is_unchanged():
    """The real seed must parse to the same numbers it always did.

    47 -> 53 is deliberate: the 6 sector_take_up rows were in the file from the
    day it was written and load() silently dropped them. See
    test_sector_rows_are_reachable.
    """
    store = Store.load()
    assert len(store.facts) == 53
    a = store.get("grade_a_rent_avg", "City")
    assert a.value == 76.21 and a.delta("yoy").render() == "+7.0%"
    sigs = detect_all(store, Watchlist.load())
    # 4 -> 5 is deliberate: sector_demand joins the sector take-up breakdown to
    # the under-offer total, a comparison the source never states. See
    # test_sector_demand.py.
    assert len(sigs) == 5
    assert "in 2026" in [s for s in sigs if s.id.startswith("supply")][0].detail


def test_sector_rows_are_reachable():
    """Data in the seed that no code path can read is data we do not have."""
    store = Store.load()
    ins = store.get("take_up", "Central London", sector="Insurance & Financial")
    assert ins is not None and ins.value == 878112
    assert ins.delta("vs_avg").render() == "-21.0%"

    # Tech & Media publishes a share and no absolute (E-4).
    tech = store.get("take_up", "Central London", sector="Tech & Media")
    assert tech.value is None and tech.extras["share_pct"] == 31.0

    # E-3 still holds: the sector-free total must not be shadowed by a slice.
    assert store.get("take_up", "Central London").sector is None


def test_events_are_reachable():
    """17 events loaded and nothing could read them; the sidebar counted them."""
    store = Store.load()
    lettings = store.find_events(type="letting", min_sqft=100_000)
    assert [e["occupier"] for e in lettings] == ["Anthropic"]
    assert lettings[0]["sqft"] == 158138
    # Heterogeneous shapes: Runway East has no building, Softbank no sqft.
    assert all("type" in e for e in store.find_events())
    assert store.event_types() == ["completion", "development_start",
                                   "investment", "letting"]


def test_unknown_top_level_key_fails_loud():
    """The fail-loud discipline must not stop at the fact level."""
    raw = json.loads((Path(__file__).resolve().parents[1]
                      / "data" / "seed_2026Q2.json").read_text())
    raw["yield_analysis_2026Q2"] = [{"anything": 1}]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "seed_bad.json"
        p.write_text(json.dumps(raw))
        try:
            Store.load([p])
        except SeedSchemaError as e:
            assert "yield_analysis_2026Q2" in str(e)
        else:
            raise AssertionError("unknown top-level key was silently ignored")


def test_new_sector_table_needs_no_code_change():
    """The period is baked into the key, so match the shape (E-6, one level up)."""
    raw = json.loads((Path(__file__).resolve().parents[1]
                      / "data" / "seed_2026Q2.json").read_text())
    del raw["sector_take_up_2026H1"]
    raw["sector_take_up_2027Q1"] = [
        {"sector": "Legal", "value": 111000, "unit": "sqft"}]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "seed_fwd.json"
        p.write_text(json.dumps(raw))
        f = Store.load([p]).get("take_up", "Central London", sector="Legal")
    assert f.value == 111000 and str(f.period) == "2027Q1"


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

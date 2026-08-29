"""The sector signal: trailing take-up disagreeing with the leading pipeline.

North star goal 3. Insurance & Financial posts its weakest take-up since H1
2021, down 21% on the five-year average -- and simultaneously holds 41% of
space under offer while making up 20% of take-up. Read alone, the first
number says a sector in retreat. Read together, they say the retreat is
already priced and the pipeline is about to turn.

Savills states both figures and never puts them side by side. That join is
the whole reason this detector exists, so these tests guard the join rather
than the arithmetic.

One number needed promoting to make it computable: "41% of space currently
under offer" lived in a note string. A detector parsing prose for a figure is
the defect this store was built to prevent, so it moved into `extras` under
`share_of_under_offer_pct` and the sentence stayed for provenance.

Run:  uv run python tests/test_sector_demand.py     (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cre_agent.signals import OPPORTUNITY, sector_demand
from cre_agent.store import Delta, Fact, Period, Source, Store
from cre_agent.submarkets import SubmarketIndex
from cre_agent.watchlist import Watchlist

SRC = Source("Test", "Fixture", "2026-08-06", "https://example.invalid")


def take_up(sector, value, share_pct, under_offer_pct, vs_avg):
    """One sector take-up row, shaped like the seed's."""
    extras = {"share_pct": share_pct}
    if under_offer_pct is not None:
        extras["share_of_under_offer_pct"] = under_offer_pct
    deltas = () if vs_avg is None else (Delta("vs_avg", "5yr_avg", vs_avg, "pct"),)
    return Fact(metric="take_up", submarket="Central London",
                period=Period.parse("2026H1"), value=value, unit="sqft",
                source=SRC, sector=sector, deltas=deltas, extras=extras)


def store_of(*facts) -> Store:
    return Store(list(facts), [], [SRC], index=SubmarketIndex.load())


# -- the shipped seed -------------------------------------------------------

def test_fires_on_the_shipped_seed():
    sigs = sector_demand(Store.load())
    assert len(sigs) == 1
    s = sigs[0]
    assert s.id == "sector_demand:Insurance & Financial"
    assert s.severity == OPPORTUNITY
    assert "41%" in s.headline and "20%" in s.headline


def test_the_41_is_data_not_prose():
    """The figure must be reachable without reading the note.

    If this fails the detector is one refactor away from parsing a sentence.
    """
    f = Store.load().get("take_up", "Central London", sector="Insurance & Financial")
    assert f.extras["share_of_under_offer_pct"] == 41.0
    assert f.extras["share_pct"] == 20.0
    assert "41%" in f.note          # the source's own wording, kept for provenance


def test_detail_cites_both_tables():
    s = sector_demand(Store.load())[0]
    metrics = {f.metric for f in s.evidence}
    assert metrics == {"take_up", "under_offer"}, "the join is the whole signal"
    assert s.citations()


def test_never_claims_a_watchlist_asset():
    """Sectors are occupier groupings, not geographies.

    supply_shock calls _match(submarket=None) and so claims all three
    buildings for a market-wide fact, which is why the brief header reads
    "N of M signals touch your portfolio" with N too high. This detector
    must not add to that.
    """
    assert sector_demand(Store.load(), Watchlist.load())[0].affected == []


def test_empty_watchlist_is_unaffected():
    empty = Watchlist([], SubmarketIndex.load())
    assert len(sector_demand(Store.load(), empty)) == 1


# -- the firing rule --------------------------------------------------------

def test_silent_when_the_pipeline_is_not_outsized():
    """25% of the pipeline on 20% of take-up is noise, not a signal."""
    s = store_of(take_up("Insurance & Financial", 878112, 20.0, 25.0, -21.0))
    assert sector_demand(s) == []


def test_silent_when_take_up_is_already_growing():
    """A sector posting records needs no campaign pointed at it."""
    s = store_of(take_up("Tech & Media", 900000, 20.0, 41.0, +12.0))
    assert sector_demand(s) == []


def test_silent_before_the_figure_was_promoted():
    """The pre-fix state: 41% present only in prose. Silence is correct."""
    s = store_of(take_up("Insurance & Financial", 878112, 20.0, None, -21.0))
    assert sector_demand(s) == []


def test_silent_without_a_trailing_delta():
    s = store_of(take_up("Insurance & Financial", 878112, 20.0, 41.0, None))
    assert sector_demand(s) == []


def test_ranks_on_the_computed_weight_not_the_headline():
    """Two candidates, capped at one. The heavier pipeline must win.

    The first draft sorted by parsing the percentage back out of its own
    headline string. It gave the right answer here and would have broken
    silently the first time the headline was reworded.
    """
    s = store_of(
        take_up("Insurance & Financial", 878112, 20.0, 35.0, -21.0),   # 1.75x
        take_up("Creative", 255000, 10.0, 30.0, -5.0),                 # 3.0x
    )
    out = sector_demand(s)
    assert len(out) == 1
    assert out[0].id == "sector_demand:Creative"


def test_no_bps_rendered_as_a_percentage():
    """E-5: the unit travels with the number."""
    s = sector_demand(Store.load())[0]
    assert "bps%" not in s.detail and "bps%" not in s.headline
    for f in s.evidence:
        for d in f.deltas:
            if d.unit == "bps":
                assert d.render() not in s.detail or "bps" in s.detail


def test_headline_and_detail_name_the_sector():
    s = sector_demand(Store.load())[0]
    assert "Insurance & Financial" in s.headline
    assert "Insurance & Financial" in s.detail
    assert "leasing campaign" in s.detail       # closes with an action, not an observation


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

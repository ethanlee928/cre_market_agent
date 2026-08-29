"""What the brief asks for, against what this system actually has.

The eight key areas are declared once, as intent. Everything else is derived at
render time from the fact store and the detector registry, so this table cannot
drift into claiming coverage that no longer exists: rename a detector and the
area it served drops to "on request" on the next run.

The honesty limit, stated rather than discovered: `detector` is resolved against
`{d.__name__ for d in DETECTORS}` and firing is checked against live signal ids,
so this proves a name matches a function that produces output. `metrics` and
`caveat` are hand-written and checked only for presence in the store. The table
proves an area is wired, not that the wiring is good.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .signals import DETECTORS, detect_all
from .store import Store
from .watchlist import Watchlist

GAP, ON_REQUEST, PARTIAL, COVERED = "gap", "on request", "partial", "covered"


@dataclass(frozen=True)
class KeyArea:
    n: int
    name: str
    metrics: tuple[str, ...] = ()
    detector: str | None = None
    tools: tuple[str, ...] = ()          # agent tools that reach this area
    caveat: str | None = None


AREAS: tuple[KeyArea, ...] = (
    KeyArea(1, "Prime / Grade A rents",
            ("prime_rent", "grade_a_rent_avg", "grade_b_rent_avg", "top_rent"),
            detector="quality_spread", tools=("get_metric",)),
    KeyArea(2, "Vacancy / availability",
            ("vacancy_rate", "supply", "supply_small_floorplate_share",
             "large_grade_a_options", "large_active_requirements"),
            detector="large_occupier_squeeze", tools=("get_metric",)),
    KeyArea(3, "Take-up / activity",
            ("take_up", "active_demand", "under_offer", "under_offer_regear"),
            detector="sector_demand", tools=("get_metric", "find_market_activity")),
    KeyArea(4, "Supply pipeline",
            ("completions", "completions_forecast", "under_construction",
             "pipeline_to_2029", "development_starts"),
            detector="supply_shock", tools=("get_metric",)),
    KeyArea(5, "Submarket dynamics",
            ("vacancy_rate", "prime_rent"),
            tools=("get_metric",),
            caveat="no Midtown or Southbank facts in any loaded source; a query "
                   "for one answers at the nearest published parent and says so. "
                   "Canary Wharf publishes via the Colliers (vacancy, 2025Q4) "
                   "and Carter Jonas (prime rent, 2026Q1) seeds"),
    KeyArea(6, "Macro (rates, gilts, Bank Rate)",
            tools=(),
            caveat="not in a quarterly agency report; no tool, and the model is "
                   "instructed to refuse rather than answer from memory"),
    KeyArea(7, "Occupier drivers",
            ("expanding_occupier_share", "grade_a_share_of_take_up",
             "breeam_exc_out_share_of_take_up", "breeam_rent_premium"),
            detector="quality_spread", tools=("get_metric",),
            caveat="flight-to-quality and ESG are covered; no hybrid-working data "
                   "is published in this source"),
    KeyArea(8, "News / named events",
            tools=("find_market_activity",),
            caveat="20 seed events (17 Savills transactions, 3 Canary Wharf "
                   "building events) plus live Search grounding; no detector, "
                   "and 13 of the 20 carry no submarket"),
    # The ninth area is an extension of the brief's spec, added on industry
    # feedback (2026-08-29): building-vs-building is the decision surface a
    # practitioner actually asked for. docs/designs/canary-wharf-peer-comps.md.
    KeyArea(9, "Peer comparison (building vs building)",
            ("rateable_value_avg",),
            detector="peer_gap", tools=("compare_building",),
            caveat="Canary Wharf roster only; valuations are VOA rateable values "
                   "on a fixed 2024 basis, not passing rents; building-level "
                   "vacancy limited to individually reported majors"),
)


@dataclass
class AreaStatus:
    area: KeyArea
    status: str
    data: str
    surfaced_by: str
    firing: bool = False
    missing: tuple[str, ...] = field(default_factory=tuple)


def assess(store: Store, watchlist: Watchlist | None = None) -> list[AreaStatus]:
    """Derive every column. Nothing here is declared except intent."""
    have = set(store.metrics())
    registered = {d.__name__ for d in DETECTORS}
    live = {s.id.split(":")[0] for s in detect_all(store, watchlist)}

    out = []
    for area in AREAS:
        present = tuple(m for m in area.metrics if m in have)
        missing = tuple(m for m in area.metrics if m not in have)
        events = "find_market_activity" in area.tools and bool(store.events)

        wired = area.detector in registered if area.detector else False
        firing = bool(area.detector) and area.detector in live

        if not present and not events:
            status = GAP
        elif not wired:
            status = ON_REQUEST
        elif not firing or area.caveat or missing:
            status = PARTIAL
        else:
            status = COVERED

        if present:
            data = f"{len(present)} of {len(area.metrics)} metrics"
        elif events:
            data = f"{len(store.events)} events"
        else:
            data = "none in source"

        if firing:
            surfaced = area.detector
        elif wired:
            surfaced = f"{area.detector} (not firing)"
        elif area.tools:
            surfaced = "chat only"
        else:
            surfaced = "not surfaced"

        out.append(AreaStatus(area, status, data, surfaced, firing, missing))
    return out


def render(store: Store, watchlist: Watchlist | None = None) -> str:
    rows = assess(store, watchlist)
    w = (2, 32, 18, 26, 11)
    head = ("#", "KEY AREA", "DATA", "SURFACED BY", "STATUS")
    rule = "+" + "+".join("-" * (n + 2) for n in w) + "+"
    lines = [rule,
             "| " + " | ".join(h.ljust(n) for h, n in zip(head, w)) + " |",
             rule]
    for r in rows:
        cells = (str(r.area.n), r.area.name, r.data, r.surfaced_by, r.status)
        lines.append("| " + " | ".join(c[:n].ljust(n) for c, n in zip(cells, w)) + " |")
        if r.area.caveat:
            for chunk in _wrap(r.area.caveat, sum(w) + 10):
                lines.append("| " + f"    {chunk}".ljust(sum(w) + 12) + " |")
    lines.append(rule)
    counts = {s: sum(1 for r in rows if r.status == s) for s in
              (COVERED, PARTIAL, ON_REQUEST, GAP)}
    lines.append("  " + " · ".join(f"{v} {k}" for k, v in counts.items() if v))
    lines.append(f"  Computed from {len(store.facts)} facts, {len(store.events)} events "
                 f"and {len(DETECTORS)} detectors at render time.")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width - 4:
            out.append(line); line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out

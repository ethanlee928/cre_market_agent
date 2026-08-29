"""The fact store. The only module that touches raw seed JSON.

Everything else, deterministic detectors and agent skills alike, queries through
here. That single choke point is why the five data defects found in review get
fixed once instead of five times:

  E-1  21 different field names encode 4 comparison concepts. Three of them mean
       "year on year": vs_prior_year_pct, yoy_change_pct, vs_h1_2025_pct. City
       Grade A rent uses the third, West End Grade A uses the second. A detector
       matching one name silently misses the other. Normalised into Delta here.
  E-2  Periods are mixed granularity (2026Q2, 2026H1, 2026). A metric asked for
       at Q2 that is only published at H1 must fall back, and say that it did,
       rather than reporting "no data" while the number sits right there.
  E-3  (metric, submarket) is not unique. active_demand/Central London appears
       four times, three carrying a sector. Scalar access must refuse to guess,
       or the brief prints the 3.1m Tech slice as the 15.7m total.
  E-4  Two facts have value: null with a delta present. West End grade_b_rent_avg
       is one of them, and it is the headline signal. Levels are Optional.
  E-5  qoq_change (raw sq ft), _bps and _pct coexist. Unit travels with the
       number so "fell 20 bps" never renders as "fell 20%".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import total_ordering
from pathlib import Path
from typing import Callable, Iterable, Literal

from .submarkets import SubmarketIndex

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

DeltaKind = Literal["qoq", "yoy", "ytd", "vs_avg", "forecast"]

# E-1 / E-5: every comparison field name in the seed, mapped to (kind, basis,
# unit). Unit None means "inherits the parent fact's unit" (raw absolute change).
# Unknown keys raise at load time on purpose: when the Q1 harvest introduces a
# 22nd spelling we want a loud failure, not a silently dropped delta.
DELTA_FIELDS: dict[str, tuple[DeltaKind, str, str | None]] = {
    "qoq_change":               ("qoq",      "prior_quarter", None),
    "qoq_change_bps":           ("qoq",      "prior_quarter", "bps"),
    "qoq_change_pct":           ("qoq",      "prior_quarter", "pct"),
    "yoy_change_pct":           ("yoy",      "prior_year",    "pct"),
    "yoy_change_bps":           ("yoy",      "prior_year",    "bps"),
    "vs_prior_year_pct":        ("yoy",      "prior_year",    "pct"),
    "vs_h1_2025_pct":           ("yoy",      "prior_year",    "pct"),
    "ytd_change_pct":           ("ytd",      "year_start",    "pct"),
    "vs_10yr_avg_pct":          ("vs_avg",   "10yr_avg",      "pct"),
    "vs_10yr_avg_bps":          ("vs_avg",   "10yr_avg",      "bps"),
    "vs_10yr_q2_avg_pct":       ("vs_avg",   "10yr_q2_avg",   "pct"),
    "vs_10yr_h1_avg_pct":       ("vs_avg",   "10yr_h1_avg",   "pct"),
    "vs_5yr_avg_pct":           ("vs_avg",   "5yr_avg",       "pct"),
    "vs_long_run_avg_pct":      ("vs_avg",   "long_run_avg",  "pct"),
    "vs_long_term_h1_avg_pct":  ("vs_avg",   "long_term_h1_avg", "pct"),
    "vs_long_run_avg_bps":      ("vs_avg",   "long_run_avg",  "bps"),
    "forecast_2026_growth_pct": ("forecast", "2026",          "pct"),
}

# E-6: some spellings name the *other* period instead of the relationship, so
# the field name carries a literal year: vs_h1_2025_pct on a 2026H1 fact just
# means year on year. Listing those literals pins the store to one calendar
# year -- the Q2 2027 report spells it vs_h1_2026_pct and the loader refuses to
# open the file at all. Match the shape, resolve the relationship against the
# fact's own period.
_VS_PERIOD = re.compile(r"^vs_(?:(?:h[12]|q[1-4])_)?(\d{4})_(pct|bps)$")
_FORECAST_YEAR = re.compile(r"^forecast_(\d{4})_growth_(pct|bps)$")


def _resolve_dated_field(key: str, fact_year: int) -> tuple[DeltaKind, str, str] | None:
    """Interpret a comparison field whose name embeds a year. None if unknown."""
    if m := _VS_PERIOD.match(key):
        if int(m[1]) == fact_year - 1:
            return "yoy", "prior_year", m[2]
        # Two or more years back is a different concept, not a rolled year.
        # Fall through to the loud SeedSchemaError rather than mislabel it.
        return None
    if m := _FORECAST_YEAR.match(key):
        return "forecast", m[1], m[2]
    return None


# Descriptive fields that are neither the value nor a delta.
EXTRA_FIELDS = {
    "transactions", "schemes", "prelet_pct", "not_started_pct",
    "absolute_sqft", "yoy_change_pct_note", "share_pct",
    # Promoted out of a note string. Savills states it in prose -- "the sector
    # is 41% of space currently under offer" -- and prose is not reachable by
    # a detector. Parsing a number back out of a sentence at runtime is the
    # thing this store exists to prevent, so the figure moves into data and
    # the sentence stays for provenance. Same defect class as the sector rows
    # in 716a779: published, transcribed, and unreadable by any code path.
    "share_of_under_offer_pct",
    # How many VOA office hereditaments a building-level rateable_value_avg
    # aggregates over. n=1 is a whole-building assessment (quantum discounts
    # embedded); n=459 is a floor-by-floor multi-let. A reader weighing the
    # figure needs the count, so it travels as data, not prose.
    "hereditaments",
}

CORE_FIELDS = {"metric", "submarket", "period", "value", "unit", "note", "sector",
               "building"}

# The roster: identity plus physical constants for a named, real building.
# Measurements about a building (rateable value, achieved rent) are ordinary
# Facts carrying `building`; the roster row is only what does not move. Same
# fail-loud discipline as facts: an unknown field raises, never drops.
BUILDING_FIELDS = {"name", "submarket", "sqft", "year_built", "floors",
                   "epc_rating", "note"}

# E-9: _parse_fact raises on an unknown field *inside* a fact, but load() read
# four known top-level keys and dropped everything else in silence -- which is
# exactly where a new harvest adds things. sector_take_up_2026H1 sat unread in
# the seed from the day it was written. Same discipline, one level up.
TOP_LEVEL_KEYS = {"source", "period", "facts", "events", "buildings"}

# Sector tables bake the period into the key, the same disease E-6 fixes for
# delta fields. Match the shape and parse the period out, so seed_2027Q1.json
# does not need a code change to be read.
_SECTOR_TABLE = re.compile(r"^sector_(\w+?)_(\d{4}(?:H[12]|Q[1-4])?)$")


class SeedSchemaError(ValueError):
    """Raised when a seed file contains a field the store does not understand."""


# --------------------------------------------------------------------------
# Period: comparable, with containment so Q2 can fall back to H1 (E-2)
# --------------------------------------------------------------------------

_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")
_HALF = re.compile(r"^(\d{4})H([12])$")
_YEAR = re.compile(r"^(\d{4})$")
_RANGE = re.compile(r"^(\d{4})(?:H[12])?-(\d{4})$")


# Grain, tightest first. Used to break ties between two periods that close at
# the same moment: 2026Q4 is a tighter reading of the same instant than 2026H2.
_GRAIN = {"quarter": 0, "half": 1, "year": 2, "range": 3}


@total_ordering
@dataclass(frozen=True)
class Period:
    year: int
    kind: str          # "quarter" | "half" | "year" | "range"
    index: int = 0     # quarter 1-4, half 1-2, else 0
    end_year: int = 0  # ranges only
    raw: str = ""

    @classmethod
    def parse(cls, s: str) -> "Period":
        s = s.strip()
        if m := _QUARTER.match(s):
            return cls(int(m[1]), "quarter", int(m[2]), raw=s)
        if m := _HALF.match(s):
            return cls(int(m[1]), "half", int(m[2]), raw=s)
        if m := _YEAR.match(s):
            return cls(int(m[1]), "year", raw=s)
        if m := _RANGE.match(s):
            return cls(int(m[1]), "range", end_year=int(m[2]), raw=s)
        raise SeedSchemaError(f"unparseable period: {s!r}")

    @property
    def months(self) -> tuple[int, int]:
        """Inclusive month span within the year, 1-12."""
        if self.kind == "quarter":
            return (self.index - 1) * 3 + 1, self.index * 3
        if self.kind == "half":
            return (1, 6) if self.index == 1 else (7, 12)
        return 1, 12

    def contains(self, other: "Period") -> bool:
        """True if `other` falls inside this period. 2026H1 contains 2026Q2."""
        if self.kind == "range":
            return self.year <= other.year <= self.end_year
        if self.year != other.year:
            return False
        if other.kind == "range":
            return False
        a, b = self.months
        c, d = other.months
        return a <= c and d <= b

    def ends(self) -> tuple[int, int]:
        """(year, month) at which the period closes.

        The chronological anchor. "Latest" means "closes last", which is the
        only definition that works across mixed grain: 2027H1 closes after
        2026Q4 even though a quarter is the tighter reading.
        """
        if self.kind == "range":
            return self.end_year, 12
        return self.year, self.months[1]

    def order_key(self) -> tuple:
        """Total, chronological ordering.

        `order=True` on the dataclass sorted on the *fields*, which put `kind`
        second and compared it as a string: "half" < "quarter" < "range" <
        "year". That made Period("2026H2") < Period("2026Q1") -- July-December
        ranked before January-March. Nothing compared Periods yet, so it never
        fired, but every "give me the most recent one" this fix adds would have
        inherited it.
        """
        return (*self.ends(), _GRAIN.get(self.kind, 9), self.raw)

    def __lt__(self, other: object):
        if not isinstance(other, Period):
            return NotImplemented
        return self.order_key() < other.order_key()

    def __str__(self) -> str:
        return self.raw or f"{self.year}"


# --------------------------------------------------------------------------
# Fact model
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Delta:
    kind: DeltaKind
    basis: str
    value: float
    unit: str

    def render(self) -> str:
        """E-5: unit travels with the number, so bps never prints as pct."""
        sign = "+" if self.value > 0 else ""
        if self.unit == "bps":
            return f"{sign}{self.value:.0f} bps"
        if self.unit == "pct":
            return f"{sign}{self.value:.1f}%"
        if self.unit == "sqft":
            return f"{sign}{self.value:,.0f} sq ft"
        return f"{sign}{self.value:g} {self.unit}"


@dataclass(frozen=True)
class Source:
    publisher: str
    title: str
    published: str
    url: str

    def cite(self) -> str:
        return f"{self.publisher}, {self.title} (as of {self.published})"


@dataclass(frozen=True)
class Fact:
    metric: str
    submarket: str
    period: Period
    value: float | None            # E-4: level may be unpublished
    unit: str
    source: Source
    sector: str | None = None      # E-3: part of the identity
    building: str | None = None    # part of the identity, the same move as
                                   # sector: two buildings' rateable values are
                                   # different facts, and a market-level lookup
                                   # must never return a building's figure
    note: str | None = None
    deltas: tuple[Delta, ...] = ()
    extras: dict = field(default_factory=dict)
    provenance: str = "seed"       # seed | grounded | model

    def delta(self, kind: DeltaKind, basis: str | None = None) -> Delta | None:
        """E-1: ask by meaning, never by the seed's field spelling."""
        for d in self.deltas:
            if d.kind == kind and (basis is None or d.basis == basis):
                return d
        return None

    def render_value(self) -> str:
        if self.value is None:
            return "not published"
        if self.unit == "pct":
            return f"{self.value:.1f}%"
        if self.unit == "gbp_psf":
            return f"£{self.value:,.2f} psf"
        if self.unit == "gbp_psm":
            # The VOA publishes per square metre. Rendered as m² so a reader
            # can never mistake it for psf; conversion is comps.psm_to_psf's
            # job, in exactly one place (invariant C-1).
            return f"£{self.value:,.2f} per m²"
        if self.unit == "sqft":
            return f"{self.value:,.0f} sq ft"
        if self.unit == "gbp":
            return f"£{self.value / 1e9:.2f}bn" if self.value >= 1e9 else f"£{self.value:,.0f}"
        if self.unit == "count":
            return f"{self.value:,.0f}"
        return f"{self.value:g}"

    def label(self) -> str:
        base = self.metric.replace("_", " ")
        where = f"{self.building}, {self.submarket}" if self.building else self.submarket
        return f"{where} {base}" + (f" [{self.sector}]" if self.sector else "")


@dataclass(frozen=True)
class Building:
    """One real, named building: the identity half of a peer comparison.

    Only what does not move lives here -- name, place, size, age, storeys,
    EPC. Anything measured or valued about the building (rateable value,
    achieved rent, a vacancy event) is a Fact or event carrying `building`,
    so it arrives with its own Source and period like every other number.
    Per-row provenance for these constants travels in `note`; the file-level
    Source covers the harvest. That is the pilot's compromise, recorded in
    docs/designs/canary-wharf-peer-comps.md.
    """
    name: str
    submarket: str
    source: Source
    sqft: int | None = None
    year_built: int | None = None
    floors: int | None = None
    epc_rating: str | None = None
    note: str | None = None


class AmbiguousQuery(LookupError):
    """E-3: a scalar lookup matched more than one fact. Refuse, never guess."""


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class Store:
    def __init__(self, facts: list[Fact], events: list[dict], sources: list[Source],
                 index: "SubmarketIndex | None" = None,
                 buildings: list[Building] | None = None):
        self.facts = facts
        self.events = events
        self.sources = sources
        self.buildings = buildings or []
        # The controlled vocabulary. Without it every submarket lookup is a
        # string compare, so "Mayfair" misses a fact filed under "West End
        # Core (Mayfair/St James's)" and the agent answers "I don't have that"
        # about a figure sitting in the store.
        self.index = index if index is not None else SubmarketIndex.load()

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, paths: Iterable[Path] | None = None) -> "Store":
        paths = list(paths) if paths else sorted(DATA_DIR.glob("seed_*.json"))
        if not paths:
            raise FileNotFoundError(f"no seed files found in {DATA_DIR}")

        facts: list[Fact] = []
        events: list[dict] = []
        sources: list[Source] = []
        buildings: list[Building] = []
        seen: set[tuple] = set()
        seen_buildings: set[str] = set()

        for path in paths:
            raw = json.loads(path.read_text())
            src = Source(**{k: raw["source"][k]
                            for k in ("publisher", "title", "published", "url")})
            sources.append(src)

            for row in raw.get("facts", []):
                fact = cls._parse_fact(row, src, path.name)
                # H5: the Q2 file already carries some Q1 rows. Dedupe on
                # identity so merging a Q1 file later cannot double-count.
                # `building` is part of the identity, or two towers' same
                # metric collide and the second one silently drops.
                key = (fact.metric, fact.submarket, str(fact.period),
                       fact.sector, fact.building)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(fact)

            for ev in raw.get("events", []):
                events.append({**ev, "_source": src})

            for row in raw.get("buildings", []):
                if unknown := sorted(set(row) - BUILDING_FIELDS):
                    raise SeedSchemaError(
                        f"{path.name}: unrecognised building field(s) {unknown} "
                        f"on {row.get('name')}. Add them to BUILDING_FIELDS in "
                        f"store.py."
                    )
                b = Building(source=src, **row)
                if b.name in seen_buildings:
                    continue
                seen_buildings.add(b.name)
                buildings.append(b)

            # Sector tables become ordinary Facts. They are take-up
            # measurements carrying a sector, and Fact already treats sector as
            # part of its identity (E-3) and already allows an unpublished
            # level (E-4) -- Tech & Media publishes a share and no absolute.
            # A parallel model would duplicate all of that.
            unknown = []
            for key in sorted(set(raw) - TOP_LEVEL_KEYS):
                # JSON has no comments. A leading underscore marks file-level
                # commentary for the human reading the seed -- harvest method,
                # aggregation rule -- deliberately unread by code, so it can
                # never masquerade as data.
                if key.startswith("_"):
                    continue
                m = _SECTOR_TABLE.match(key)
                if not m:
                    unknown.append(key)
                    continue
                metric, period = m[1], Period.parse(m[2])
                for row in raw[key]:
                    # Defaults first so a row's own values win; metric and
                    # period last because the key, not the row, defines them.
                    fact = cls._parse_fact(
                        {"submarket": "Central London", "unit": "sqft", **row,
                         "metric": metric, "period": str(period)},
                        src, path.name)
                    key_id = (fact.metric, fact.submarket, str(fact.period),
                              fact.sector, fact.building)
                    if key_id in seen:
                        continue
                    seen.add(key_id)
                    facts.append(fact)

            if unknown:
                raise SeedSchemaError(
                    f"{path.name}: unrecognised top-level key(s) {unknown}. "
                    f"Add them to TOP_LEVEL_KEYS in store.py, or to the "
                    f"_SECTOR_TABLE shape if they are sector breakdowns. "
                    f"Silently ignoring them is how 6 rows went unread."
                )

        return cls(facts, events, sources, buildings=buildings)

    @staticmethod
    def _parse_fact(row: dict, src: Source, filename: str) -> Fact:
        period = Period.parse(row["period"])
        unit = row.get("unit", "")

        # Literal spellings first, so existing seeds parse byte-identically.
        deltas = []
        for key, (kind, basis, dunit) in DELTA_FIELDS.items():
            if (v := row.get(key)) is not None:
                deltas.append(Delta(kind, basis, float(v), dunit or unit))

        # Then the year-bearing shapes (E-6). Anything still unrecognised is a
        # genuinely new concept and still fails loud.
        unknown = []
        for key in sorted(set(row) - CORE_FIELDS - set(DELTA_FIELDS) - EXTRA_FIELDS):
            spec = _resolve_dated_field(key, period.year)
            if spec is None:
                unknown.append(key)
            elif (v := row.get(key)) is not None:
                kind, basis, dunit = spec
                deltas.append(Delta(kind, basis, float(v), dunit or unit))

        if unknown:
            raise SeedSchemaError(
                f"{filename}: unrecognised field(s) {unknown} on "
                f"{row.get('metric')}/{row.get('submarket')}. Add them to "
                f"DELTA_FIELDS or EXTRA_FIELDS in store.py."
            )

        return Fact(
            metric=row["metric"],
            submarket=row["submarket"],
            period=period,
            value=None if row.get("value") is None else float(row["value"]),
            unit=unit,
            source=src,
            sector=row.get("sector"),
            building=row.get("building"),
            note=row.get("note"),
            deltas=tuple(deltas),
            extras={k: row[k] for k in EXTRA_FIELDS if k in row},
        )

    # -- querying ---------------------------------------------------------

    def find(
        self,
        metric: str | None = None,
        submarket: str | None = None,
        period: str | Period | None = None,
        sector: str | None = "__any__",
        building: str | None = "__any__",
    ) -> list[Fact]:
        """All matching facts. `sector=None` means only sector-free totals,
        and `building=None` means only building-free market figures -- the
        same refuse-to-guess default, third verse."""
        if isinstance(period, str):
            period = Period.parse(period)

        out = []
        for f in self.facts:
            if metric and f.metric != metric:
                continue
            if submarket and not self._same_submarket(f.submarket, submarket):
                continue
            if sector != "__any__" and f.sector != sector:
                continue
            if building != "__any__" and f.building != building:
                continue
            if period and not (f.period == period or period.contains(f.period)
                               or f.period.contains(period)):
                continue
            out.append(f)
        return out

    def get(
        self,
        metric: str,
        submarket: str,
        period: str | Period | None = None,
        sector: str | None = None,
        building: str | None = None,
        climb: bool = False,
    ) -> Fact | None:
        """One fact, or None.

        E-3: defaults to sector=None (the total), so `active_demand` never
        silently returns the Tech & Media slice. Raises if still ambiguous.
        Defaults building=None the same way: a market-level question never
        silently answers with one tower's figure.
        E-2: an exact-period miss falls back to the enclosing period.
        E-7: which tie-break applies depends on what was asked. See _rank.

        climb=True walks up the submarket hierarchy when the exact node holds
        nothing: ask for Paddington, get the West End figure. Sources publish
        at coarse granularity, so the nearest ancestor is usually the honest
        answer -- and the Fact carries its own submarket, so any sentence
        built from it says which geography it is really describing.

        Default False. Detectors ask about a named submarket and mean that
        submarket; silently answering about its parent would invent a figure
        the source never published for the place that was asked about.
        """
        hit = self._get_at(metric, submarket, period, sector, building)
        if hit is not None or not climb or self.index is None:
            return hit
        sid = self.index.resolve(submarket)
        if sid is None:
            return None
        for ancestor in self.index.ancestors(sid)[1:]:
            hit = self._get_at(metric, self.index.label(ancestor), period, sector,
                               building)
            if hit is not None:
                return hit
        return None

    def _get_at(
        self,
        metric: str,
        submarket: str,
        period: str | Period | None = None,
        sector: str | None = None,
        building: str | None = None,
    ) -> Fact | None:
        """One fact at exactly this node. The body get() always had."""
        exact = self.find(metric, submarket, period, sector, building)
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            rank = self._rank(period)
            exact.sort(key=rank)
            if rank(exact[0]) != rank(exact[1]):
                return exact[0]
            raise AmbiguousQuery(
                f"{metric}/{submarket} matched {len(exact)} facts: "
                f"{[(str(f.period), f.sector) for f in exact]}. "
                f"Pass period= or sector= to disambiguate."
            )
        return None

    def _same_submarket(self, fact_submarket: str, query: str) -> bool:
        """Same node, reached through the vocabulary rather than by spelling.

        Falls back to a plain string compare when either name is outside
        submarkets.yaml, so an unrecognised submarket behaves exactly as it
        did before this method existed.
        """
        if fact_submarket == query:
            return True
        if self.index is None:
            return False
        return self.index.same_node(fact_submarket, query)

    def resolve_submarket(self, name: str) -> str | None:
        """Canonical id for a submarket name, or None if it is not vocabulary.

        Lets a caller tell "I have never heard of Basingstoke" apart from
        "Canary Wharf is a real submarket and this source publishes nothing
        for it". The second is a correct answer; the first is a typo.
        """
        return self.index.resolve(name) if self.index else None

    @staticmethod
    def _rank(period) -> Callable[[Fact], tuple]:
        """E-7: two different questions wear the same method name.

            period given    "the figure as at 2026Q2". find() has already cut
                            the candidates down to ones overlapping it, so the
                            tightest reading wins: an exact Q2 beats the H1
                            that encloses it. That is E-2.

            period omitted  "the latest figure". Recency has to dominate and
                            grain may only break ties. Ranking grain first
                            returned a 2026Q2 fact over a 2027H1 one, printing
                            last year's number under this year's as-of date
                            with no error -- the exact failure this store's
                            docstring exists to prevent. Metrics do change
                            grain between reports, so this is not theoretical.
        """
        def recency(f: "Fact") -> tuple[int, int]:
            year, month = f.period.ends()
            return -year, -month

        def grain(f: "Fact") -> int:
            return _GRAIN.get(f.period.kind, 9)

        if period is None:
            return lambda f: (recency(f), grain(f))
        return lambda f: (grain(f), recency(f))

    def get_pair(
        self,
        metric_a: str,
        metric_b: str,
        submarket: str,
        sector: str | None = None,
    ) -> tuple[Fact, Fact] | None:
        """Two metrics at the newest period where BOTH are published.

        E-8: once a second quarter is loaded, get() answers each metric on its
        own and will happily hand back a 2027 Grade A beside a 2026 Grade B.
        Subtracting those and calling the result "the gap over the year" is a
        fabricated number that no source supports. Any detector differencing
        two metrics must come through here.
        """
        a = {f.period: f for f in self.find(metric_a, submarket, sector=sector)}
        b = {f.period: f for f in self.find(metric_b, submarket, sector=sector)}
        shared = set(a) & set(b)
        if not shared:
            return None
        newest = max(shared)
        return a[newest], b[newest]

    def find_events(self, type: str | None = None, sector: str | None = None,
                    submarket: str | None = None, min_sqft: int | None = None,
                    limit: int = 10) -> list[dict]:
        """Named market activity: who took space, what completed, what sold.

        17 of these loaded from the seed and nothing could read them. The
        sidebar printed a count while the agent had no way to answer "who is
        taking space right now" -- with Anthropic, OpenAI and Barclays sitting
        in the file. Read-only, like every tool the agent can reach (H7).

        Sorted by size descending: for this reader the biggest deal is the lede.
        """
        out = [e for e in self.events
               if (not type or e.get("type") == type)
               and (not sector or e.get("sector") == sector)
               and (not submarket or self._event_in(e, submarket))
               and (not min_sqft or (e.get("sqft") or 0) >= min_sqft)]
        out.sort(key=lambda e: e.get("sqft") or 0, reverse=True)
        return out[:limit]

    def _event_in(self, e: dict, query: str) -> bool:
        """Is this event inside the queried submarket?

        Downward, unlike facts. Anthropic's 158,138 sq ft at 1 Triton Square
        is filed under North of Oxford Street East, which submarkets.yaml
        declares a child of the West End -- so a West End question must reach
        it. Comparing the two strings directly, as this did, dropped the
        largest letting in the report from its own submarket's answer.

        An event with no submarket never matches a submarket filter. 13 of the
        17 in this seed have none, which is why callers should report
        events_missing_submarket() alongside a filtered count rather than let
        the absence pass silently.
        """
        es = e.get("submarket")
        if not es:
            return False
        if es == query:
            return True
        return self.index is not None and self.index.covers(es, query)

    def find_buildings(self, submarket: str | None = None) -> list[Building]:
        """The roster inside a submarket. Downward, like events: a building
        stands at a point inside a submarket, never above it, so a Docklands
        question must reach a roster filed under Canary Wharf."""
        if submarket is None:
            return list(self.buildings)
        out = []
        for b in self.buildings:
            if self._same_submarket(b.submarket, submarket) or (
                    self.index is not None
                    and self.index.covers(b.submarket, submarket)):
                out.append(b)
        return out

    def events_missing_submarket(self) -> int:
        """How many events the source files without a submarket."""
        return sum(1 for e in self.events if not e.get("submarket"))

    def event_types(self) -> list[str]:
        return sorted({e["type"] for e in self.events if "type" in e})

    def submarkets(self) -> list[str]:
        return sorted({f.submarket for f in self.facts})

    def metrics(self) -> list[str]:
        return sorted({f.metric for f in self.facts})

    def sectors(self) -> list[str]:
        """Sectors the store can actually answer for.

        Advertising a sector the agent cannot then fetch is worse than not
        advertising it: the model asks, gets the undifferentiated total, and
        reports it as the sector figure.
        """
        return sorted({f.sector for f in self.facts if f.sector})

    def newest_source(self) -> Source:
        """The most recently published source backing this store."""
        return max(self.sources, key=lambda s: s.published)

    def as_of(self) -> str:
        """Publication date of the newest source. Drives the staleness banner."""
        return self.newest_source().published

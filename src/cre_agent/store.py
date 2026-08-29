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
}

CORE_FIELDS = {"metric", "submarket", "period", "value", "unit", "note", "sector"}

# E-9: _parse_fact raises on an unknown field *inside* a fact, but load() read
# four known top-level keys and dropped everything else in silence -- which is
# exactly where a new harvest adds things. sector_take_up_2026H1 sat unread in
# the seed from the day it was written. Same discipline, one level up.
TOP_LEVEL_KEYS = {"source", "period", "facts", "events"}

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
        if self.unit == "sqft":
            return f"{self.value:,.0f} sq ft"
        if self.unit == "gbp":
            return f"£{self.value / 1e9:.2f}bn" if self.value >= 1e9 else f"£{self.value:,.0f}"
        if self.unit == "count":
            return f"{self.value:,.0f}"
        return f"{self.value:g}"

    def label(self) -> str:
        base = self.metric.replace("_", " ")
        return f"{self.submarket} {base}" + (f" [{self.sector}]" if self.sector else "")


class AmbiguousQuery(LookupError):
    """E-3: a scalar lookup matched more than one fact. Refuse, never guess."""


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class Store:
    def __init__(self, facts: list[Fact], events: list[dict], sources: list[Source]):
        self.facts = facts
        self.events = events
        self.sources = sources

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, paths: Iterable[Path] | None = None) -> "Store":
        paths = list(paths) if paths else sorted(DATA_DIR.glob("seed_*.json"))
        if not paths:
            raise FileNotFoundError(f"no seed files found in {DATA_DIR}")

        facts: list[Fact] = []
        events: list[dict] = []
        sources: list[Source] = []
        seen: set[tuple] = set()

        for path in paths:
            raw = json.loads(path.read_text())
            src = Source(**{k: raw["source"][k]
                            for k in ("publisher", "title", "published", "url")})
            sources.append(src)

            for row in raw.get("facts", []):
                fact = cls._parse_fact(row, src, path.name)
                # H5: the Q2 file already carries some Q1 rows. Dedupe on
                # identity so merging a Q1 file later cannot double-count.
                key = (fact.metric, fact.submarket, str(fact.period), fact.sector)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(fact)

            for ev in raw.get("events", []):
                events.append({**ev, "_source": src})

            # Sector tables become ordinary Facts. They are take-up
            # measurements carrying a sector, and Fact already treats sector as
            # part of its identity (E-3) and already allows an unpublished
            # level (E-4) -- Tech & Media publishes a share and no absolute.
            # A parallel model would duplicate all of that.
            unknown = []
            for key in sorted(set(raw) - TOP_LEVEL_KEYS):
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
                              fact.sector)
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

        return cls(facts, events, sources)

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
    ) -> list[Fact]:
        """All matching facts. `sector=None` means only sector-free totals."""
        if isinstance(period, str):
            period = Period.parse(period)

        out = []
        for f in self.facts:
            if metric and f.metric != metric:
                continue
            if submarket and f.submarket != submarket:
                continue
            if sector != "__any__" and f.sector != sector:
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
    ) -> Fact | None:
        """One fact, or None.

        E-3: defaults to sector=None (the total), so `active_demand` never
        silently returns the Tech & Media slice. Raises if still ambiguous.
        E-2: an exact-period miss falls back to the enclosing period.
        E-7: which tie-break applies depends on what was asked. See _rank.
        """
        exact = self.find(metric, submarket, period, sector)
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
               and (not submarket or e.get("submarket") == submarket)
               and (not min_sqft or (e.get("sqft") or 0) >= min_sqft)]
        out.sort(key=lambda e: e.get("sqft") or 0, reverse=True)
        return out[:limit]

    def event_types(self) -> list[str]:
        return sorted({e["type"] for e in self.events if "type" in e})

    def submarkets(self) -> list[str]:
        return sorted({f.submarket for f in self.facts})

    def metrics(self) -> list[str]:
        return sorted({f.metric for f in self.facts})

    def newest_source(self) -> Source:
        """The most recently published source backing this store."""
        return max(self.sources, key=lambda s: s.published)

    def as_of(self) -> str:
        """Publication date of the newest source. Drives the staleness banner."""
        return self.newest_source().published

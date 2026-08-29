"""The relevance lens: which market facts touch the assets you actually hold.

Without this the product is a nicer way to read a Savills report. With it,
"West End Grade B rents fell 11.3%" becomes "and you hold two of them".

Two review findings shape this file:

  H4  Submarkets have no controlled vocabulary in the seed. "West End" and
      "West End Core (Mayfair/St James's)" are different strings for
      overlapping places. An asset in Mayfair must match a signal about the
      West End, or the whole relevance story silently returns nothing.
  H8  yaml.load on a user-editable file is remote code execution.
      safe_load only, everywhere, always.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from pathlib import Path

import yaml

# SubmarketIndex lives in submarkets.py -- the store and the event lookup
# need it too. Imported here for this module's own use, and re-exported so
# existing `from .watchlist import SubmarketIndex` imports keep working.
from .submarkets import CONFIG_DIR, SubmarketIndex


class WatchlistSchemaError(ValueError):
    """Raised when watchlist.yaml carries a field an Asset cannot accept."""


def parse_ym(value: str) -> tuple[int, int]:
    """'2027-09' -> (2027, 9). Fails loud, as the store does everywhere else.

    Deliberately not routed through store.Period. Verified: Period.parse
    ("2027-09") raises SeedSchemaError, and Period.parse("2026H2-2029")
    discards the H2 -- .months returns (1, 12) -- so contains() answers True
    for a window that does not open until July. Lease dates are compared as
    (year, month) tuples instead.
    """
    try:
        year, month = (int(part) for part in value.split("-"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"lease date {value!r} is not YYYY-MM") from None
    if not 1 <= month <= 12:
        raise ValueError(f"lease date {value!r} has month {month}")
    return year, month


@dataclass(frozen=True)
class Asset:
    name: str
    submarket: str
    grade: str | None = None
    sqft: int | None = None
    year_built: int | None = None   # feeds the peer matcher's age band; an
                                    # asset without one skips that rule, and
                                    # the comparison says so
    lease_expiry: str | None = None
    break_date: str | None = None
    passing_rent_psf: float | None = None
    # The asset's own valuation in £/m², a user-supplied FALLBACK for the peer
    # comparison (comps C-3). comps.compare prefers the store: a real holding
    # sits in the same VOA list as its peers, so leave this unset and let both
    # sides of the gap carry the same source. A value here renders labelled as
    # the user's own figure.
    rateable_value_psm: float | None = None
    epc_rating: str | None = None
    note: str | None = None

    def lease_event(self) -> str | None:
        """The date that actually forces a decision.

        A break is a decision point; an expiry is the backstop. Whichever comes
        first is what a landlord plans around, so the break wins when present.
        An asset with neither is not on any clock and matches no window.
        """
        return self.break_date or self.lease_expiry

    def describe(self) -> str:
        bits = [self.submarket]
        if self.grade:
            bits.append(f"Grade {self.grade}")
        if self.sqft:
            bits.append(f"{self.sqft:,} sq ft")
        if self.break_date:
            bits.append(f"break {self.break_date}")
        elif self.lease_expiry:
            bits.append(f"expires {self.lease_expiry}")
        if self.epc_rating:
            bits.append(f"EPC {self.epc_rating}")
        return " · ".join(bits)


def _asset(row: dict, n: int, path: Path) -> Asset:
    """One Asset, or a message naming the field that is wrong.

    `Asset(**row)` raised a bare TypeError -- "unexpected keyword argument
    'passing_rent'" -- naming no file, no line and no fix. This is the one
    file the README asks a non-technical user to edit, and app.py loads it at
    module scope, so a single mistyped key took down the brief, the sidebar
    and chat behind a Streamlit traceback.

    Still fails loud: a watchlist that half-loads is a portfolio silently
    missing a building, which is worse. Same discipline as
    store.SeedSchemaError, and the same duty to say where and what to do.
    """
    if not isinstance(row, dict):
        raise WatchlistSchemaError(
            f"{path.name}: asset {n} is a {type(row).__name__}, not a mapping. "
            f"Each entry under `assets:` needs at least `name:` and "
            f"`submarket:`.")

    known = {f.name for f in fields(Asset)}
    where = f"{path.name}: asset {n} ({row.get('name') or 'unnamed'})"
    if unknown := sorted(set(row) - known):
        raise WatchlistSchemaError(
            f"{where} has unrecognised field(s) {unknown}. Valid fields are "
            f"{sorted(known)}. Rent-roll fields are deliberately optional -- "
            f"omit one rather than guessing it.")
    if missing := sorted(f.name for f in fields(Asset)
                         if f.default is MISSING and f.name not in row):
        raise WatchlistSchemaError(
            f"{where} is missing required field(s) {missing}.")
    return Asset(**row)


class Watchlist:
    def __init__(self, assets: list[Asset], index: SubmarketIndex, label: str = ""):
        self.assets = assets
        self.index = index
        self.label = label

    @classmethod
    def load(cls, path: Path | None = None,
             index: SubmarketIndex | None = None) -> "Watchlist":
        path = path or CONFIG_DIR / "watchlist.yaml"
        index = index or SubmarketIndex.load()
        if not path.exists():
            return cls([], index)
        raw = yaml.safe_load(path.read_text()) or {}
        assets = [_asset(row, n, path)
                  for n, row in enumerate(raw.get("assets", []) or [], 1)]
        return cls(assets, index, raw.get("label", ""))

    def __len__(self) -> int:
        return len(self.assets)

    def matching(self, submarket: str | None = None, grade: str | None = None,
                 min_sqft: int | None = None,
                 lease_event_between: tuple[str, str] | None = None) -> list[Asset]:
        """Assets a signal touches. Empty watchlist returns [].

        Returns Asset objects, not names: a caller building a per-asset reason
        needs the passing rent and the EPC, and a second lookup by name is a
        drift bug waiting for the first duplicate building name.
        """
        out = []
        for a in self.assets:
            if submarket and not self.index.covers(a.submarket, submarket):
                continue
            if grade and a.grade != grade:
                continue
            if min_sqft and (a.sqft or 0) < min_sqft:
                continue
            if lease_event_between:
                event = a.lease_event()
                if event is None:
                    continue
                lo, hi = (parse_ym(b) for b in lease_event_between)
                if not lo <= parse_ym(event) <= hi:
                    continue
            out.append(a)
        return out

    def summary(self) -> str:
        if not self.assets:
            return "No assets on the watchlist. Showing the market-wide view."
        return "; ".join(f"{a.name} ({a.describe()})" for a in self.assets)

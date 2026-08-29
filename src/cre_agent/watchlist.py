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

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class Submarket:
    id: str
    label: str
    parent: str | None = None
    aliases: tuple[str, ...] = ()


class SubmarketIndex:
    """Canonical submarket names, their aliases, and their hierarchy."""

    def __init__(self, entries: list[Submarket]):
        self.by_id = {s.id: s for s in entries}
        self._lookup: dict[str, str] = {}
        for s in entries:
            for name in (s.id, s.label, *s.aliases):
                self._lookup[name.lower()] = s.id

    @classmethod
    def load(cls, path: Path | None = None) -> "SubmarketIndex":
        path = path or CONFIG_DIR / "submarkets.yaml"
        raw = yaml.safe_load(path.read_text()) or {}
        return cls([
            Submarket(
                id=k,
                label=v.get("label", k),
                parent=v.get("parent"),
                aliases=tuple(v.get("aliases", [])),
            )
            for k, v in raw.get("submarkets", {}).items()
        ])

    def resolve(self, name: str) -> str | None:
        return self._lookup.get(name.strip().lower())

    def ancestors(self, sid: str) -> list[str]:
        """The submarket plus every parent above it."""
        out, cur = [], sid
        while cur:
            out.append(cur)
            cur = self.by_id[cur].parent if cur in self.by_id else None
        return out

    def covers(self, asset_submarket: str, signal_submarket: str) -> bool:
        """Does a signal about `signal_submarket` reach an asset in it?

        Mayfair is inside the West End, so a West End signal reaches it.
        """
        a, s = self.resolve(asset_submarket), self.resolve(signal_submarket)
        if not a or not s:
            return False
        return s in self.ancestors(a)


@dataclass(frozen=True)
class Asset:
    name: str
    submarket: str
    grade: str | None = None
    sqft: int | None = None
    lease_expiry: str | None = None
    note: str | None = None

    def describe(self) -> str:
        bits = [self.submarket]
        if self.grade:
            bits.append(f"Grade {self.grade}")
        if self.sqft:
            bits.append(f"{self.sqft:,} sq ft")
        if self.lease_expiry:
            bits.append(f"expires {self.lease_expiry}")
        return " · ".join(bits)


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
        assets = [Asset(**a) for a in raw.get("assets", [])]
        return cls(assets, index, raw.get("label", ""))

    def __len__(self) -> int:
        return len(self.assets)

    def matching(self, submarket: str | None = None, grade: str | None = None,
                 min_sqft: int | None = None) -> list[str]:
        """Names of assets a signal touches. Empty watchlist returns []."""
        out = []
        for a in self.assets:
            if submarket and not self.index.covers(a.submarket, submarket):
                continue
            if grade and a.grade != grade:
                continue
            if min_sqft and (a.sqft or 0) < min_sqft:
                continue
            out.append(a.name)
        return out

    def summary(self) -> str:
        if not self.assets:
            return "No assets on the watchlist. Showing the market-wide view."
        return "; ".join(f"{a.name} ({a.describe()})" for a in self.assets)

"""The controlled vocabulary for London office submarkets.

Lifted out of watchlist.py because it is not a watchlist concern. Three
callers need it now: the watchlist (does this signal touch my building?),
the fact store (the user typed "Mayfair"; the source says "West End Core
(Mayfair/St James's)"), and the event lookup (Anthropic's letting is filed
under "North of Oxford Street East"; the user asked about the West End).

  H4  The seed uses several strings for overlapping places. Without a
      hierarchy an asset in Mayfair never matches a signal about the West
      End, and the relevance lens silently returns nothing.
  H8  yaml.load on a user-editable file is remote code execution.
      safe_load only, everywhere, always.

Resolution runs in opposite directions for the two kinds of record, and
getting that backwards is the whole bug this module exists to fix:

    FACTS resolve UPWARD.    Ask for Paddington, accept a West End figure.
                             Sources publish at coarse granularity, so the
                             nearest ancestor is usually the honest answer.
                             The Fact carries its own submarket, so an answer
                             built from it labels itself.

    EVENTS resolve DOWNWARD. Ask for the West End, accept a letting filed
                             under North of Oxford Street East. A deal happens
                             at a point inside a submarket, never above it.
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

    def label(self, sid: str) -> str:
        entry = self.by_id.get(sid)
        return entry.label if entry else sid

    def ancestors(self, sid: str) -> list[str]:
        """The submarket plus every parent above it, nearest first."""
        out, cur = [], sid
        while cur:
            out.append(cur)
            cur = self.by_id[cur].parent if cur in self.by_id else None
        return out

    def covers(self, inner: str, outer: str) -> bool:
        """Does `outer` contain `inner`? Mayfair is inside the West End.

        Used for the downward direction: an event or an asset sitting at
        `inner` is reached by a question about `outer`.
        """
        a, s = self.resolve(inner), self.resolve(outer)
        if not a or not s:
            return False
        return s in self.ancestors(a)

    def same_node(self, a: str, b: str) -> bool:
        """Two strings naming the same submarket. Neither may be unknown."""
        ra, rb = self.resolve(a), self.resolve(b)
        return ra is not None and ra == rb

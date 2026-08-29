"""Programmatic graders for the live evals. Pure functions, no network.

The honesty limit, stated rather than discovered: figures_sourced() proves
that every figure-like token in an answer also appears in the tool traffic
that produced it. It cannot see inside a grounded web page, so when an answer
carries web citations, unmatched tokens downgrade to warnings for a human to
read -- the harness never guesses whether the web said it. Two more stated
limits: a bare 4-digit number is treated as a year and skipped (a real figure
that size is written with a comma in every source this store holds), and bare
integers under 13 are not figures (counts of signals, months, "2 or 3
sentences"). Both are the price of never flagging "2026" as a hallucination.
"""

from __future__ import annotations

import re

from cre_agent.signals import ACTIONS

# Strip before extraction: ISO dates (as-of dates, lease dates), then period
# tokens, bare years and decades. Order matters -- "2026-08-06" must go as one
# token, not leave "-08" behind to become a figure. Decades ("1950s" for a
# building whose sourced year_built is 1958) are the year rule again: a decade
# paraphrase of a sourced year is not an invented figure.
_DATES = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_PERIODS = re.compile(r"\b\d{4}(?:[QH]\d|s)?\b|\b[QH][1-4]\b")

_MULT = {"m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9, "k": 1e3}

# The lookbehind keeps digits inside identifiers out: "E14" is a postcode,
# not a fourteen. The lookahead drops ordinals -- "the 14th floor" is an
# address, and flagging it failed a perfectly honest refusal in the first
# 3-rep run.
_FIGURE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<cur>£|\$)?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?!(?:st|nd|rd|th)\b)\s*"
    r"(?P<mult>million|billion|bn|m\b|k\b)?\s*"
    r"(?P<unit>%|percent\b|bps\b|basis points|psf\b|per sq\s?ft|sq\s?ft"
    r"|points\b|:\s?1\b|to 1\b|times\b|x\b)?",
    re.I)


def _tokens(text: str) -> list[tuple[str, float, bool]]:
    """(raw token, absolute value, looks-like-a-figure) for every number."""
    clean = _PERIODS.sub(" ", _DATES.sub(" ", text))
    out = []
    for m in _FIGURE.finditer(clean):
        value = float(m["num"].replace(",", ""))
        if mult := m["mult"]:
            value *= _MULT[mult.lower().strip()]
        anchored = bool(m["cur"] or m["mult"] or m["unit"])
        out.append((m.group(0).strip(), abs(value), anchored or value >= 13))
    return out


def stated_figures(answer: str) -> list[tuple[str, float]]:
    """Figure-like tokens the answer asserts. These need a source."""
    return [(raw, v) for raw, v, is_figure in _tokens(answer) if is_figure]


def allowed_figures(trace: list[tuple[str, dict]], results: list,
                    question: str = "") -> set[float]:
    """Every number the tool traffic put in front of the model.

    Walks tool results recursively, including numbers embedded in strings --
    why_each_asset and signal detail arrive as prose carrying figures, and a
    figure the model repeats from there is sourced, not invented.
    """
    out: set[float] = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, bool):
            pass
        elif isinstance(x, (int, float)):
            out.add(abs(float(x)))
        elif isinstance(x, str):
            for _, v, _ in _tokens(x):
                out.add(v)

    walk(results)
    for _, args in trace:
        walk(dict(args))
    for _, v, _ in _tokens(question):
        out.add(v)
    return out


def _matches(stated: float, allowed: set[float]) -> bool:
    for a in allowed:
        if a == stated:
            return True
        # The model restating 919,980 as "roughly £920,000" is rounding a
        # sourced figure, not inventing one.
        if stated and abs(a - stated) / max(a, stated) < 0.002:
            return True
        if stated == round(a):
            return True
    return False


def _complement_of_sourced_pct(raw: str, value: float,
                               allowed: set[float]) -> bool:
    """"65% is not pre-let" restates a sourced "35% pre-let". A percentage
    complement asserts nothing the source did not -- it cannot be wrong when
    the source is right -- so it is the rounding rule, not an invention.
    Percent-shaped tokens only, both sides inside [0, 100]."""
    if "%" not in raw and "percent" not in raw.lower():
        return False
    if not 0 <= value <= 100:
        return False
    return any(0 <= a <= 100 and abs((100 - a) - value) < 0.05
               for a in allowed)


def figures_sourced(answer: str, allowed: set[float],
                    has_citations: bool) -> tuple[list[str], list[str]]:
    """(failures, warnings): stated tokens with no source in the trace."""
    failures, warnings = [], []
    for raw, value in stated_figures(answer):
        if _matches(value, allowed):
            continue
        if _complement_of_sourced_pct(raw, value, allowed):
            continue
        (warnings if has_citations else failures).append(raw)
    return failures, warnings


def action_verb(answer: str) -> bool:
    """Does the answer close on a verb from the closed vocabulary?

    Whole words only. A substring test passed any answer containing
    "holdings" -- which is what this portfolio is called -- so the check was
    close to vacuous on the answers it most needed to grade.
    """
    norm = answer.lower().replace("-", "")
    return any(re.search(rf"\b{re.escape(a.replace('-', ''))}\b", norm)
               for a in ACTIONS)


def forbidden(answer: str, patterns: list[str]) -> list[str]:
    return [p for p in patterns if re.search(p, answer, re.I)]


def required(answer: str, patterns: list[str]) -> list[str]:
    """Patterns the answer must match; returns the ones it missed."""
    return [p for p in patterns if not re.search(p, answer, re.I)]


def called(trace: list[tuple[str, dict]], matcher: dict) -> bool:
    """Did any call satisfy this matcher?

    tool: "get_signals" or alternatives "get_signals|get_watchlist".
    args_include: every key must be present with this value (string compare).
    args_absent: these keys must be missing or empty on the matched call.
    """
    names = matcher["tool"].split("|")
    include = matcher.get("args_include", {})
    absent = matcher.get("args_absent", [])
    for name, args in trace:
        if name not in names:
            continue
        if any(str(args.get(k)) != str(v) for k, v in include.items()):
            continue
        if any(args.get(k) not in (None, "") for k in absent):
            continue
        return True
    return False

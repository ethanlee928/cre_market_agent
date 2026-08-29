# London Office Market Monitor

An AI agent that monitors the Central London office market for a commercial real
estate team. Opens with a ranked brief filtered to the buildings the user holds,
then answers follow-ups with sourced figures.

## Architecture: deterministic spine, agentic edges

This is the governing decision (design doc, Approach C). Hold the line on it.

**Plain Python — must be identical on every run, and must work with no API key:**
- `src/cre_agent/store.py` — fact store, period parsing, delta normalisation
- `src/cre_agent/signals.py` — detectors + severity ranking
- `src/cre_agent/watchlist.py` — asset loading, lease windows, relevance matching
- `src/cre_agent/submarkets.py` — the controlled vocabulary and its hierarchy
- Every figure rendered on the brief page, including the per-building reversion
  arithmetic and its decision verb

**Gemini 3.7 Flash — interpretation only:**
- Narrative for each signal, chat, news via Search grounding
- The agent narrates; it never computes. Every figure comes from a tool call.

## Non-negotiables

- **P3 — every number carries its source and as-of date.** An unsourced figure is
  a liability. "I don't have that" is a correct answer.
- **Empty watchlist must stay fully functional.** Market-wide is the base case;
  the watchlist is additive. Hard requirement, not a nicety.
- **Fictional holdings must be labelled fictional wherever they render.** Every
  market figure is real and sourced; the three assets in `config/watchlist.yaml`
  are not — and neither are their passing rents, break dates or EPC ratings, which
  are the inputs to the reversion figures on the brief. That labelling is the
  honesty guarantee.
- **`yaml.safe_load` only.** `config/` is user-editable; `yaml.load` is RCE.
- **Units travel with numbers.** Use `Delta.render()`. Never f-string a raw bps
  value — it will print as a percentage and be wrong by 100x.
- **Levels may be unpublished, and the Fact is still truthy.** `Fact.value` can be
  `None` with deltas present (e.g. West End vacancy, West End `grade_b_rent_avg`).
  Read the delta; never assume a level. The test is always `f is not None and
  f.value is not None` — a bare `if fact:` passes and the sentence downstream then
  asserts a comparison it never made. `quality_spread` and `_reversion` are the
  reference implementations.
- **A signal may only claim an asset it can name a reason for.** `Signal.affected`
  travels with `match_reasons` and `match_actions`, both filled in the detector.
  `supply_shock` used to match on `submarket=None` — a filter on nothing — and so
  claimed the whole portfolio. An unjustified exposure is the same class of defect
  as an unsourced figure.
- **The decision verb comes from `signals.ACTIONS`, built in Python.** Not from
  `SYSTEM_PROMPT`: the brief renders on the no-API-key path. "monitor" is not in
  the vocabulary and a test asserts it never appears.
- **Lease dates are `(year, month)` tuples, never `Period`.** `Period.parse
  ("2027-09")` raises, and `Period.parse("2026H2-2029")` discards the `H2`, so
  `contains()` answers True for Q1 of a window that opens in July. Use
  `watchlist.parse_ym`, and read window bounds off `Period.raw`.
- **Do not remove `include_server_side_tool_invocations=True`** from `tool_config`
  in `llm/gemini.py`. Mixing our function declarations with Google Search
  grounding in one request returns 400 without it.

## Entry point

One surface: `uv run streamlit run app.py`. There is deliberately no CLI — see
the comment in `pyproject.toml`.

## Data

`data/seed_2026Q2.json` — Savills Central London Office Market Watch Q2 2026,
published 2026-08-06. 70 source records — 47 facts, 6 sector rows, 17 events —
which load as 53 Facts and 17 events (sector rows normalise into Facts; a Fact
treats `sector` as part of its identity). Real, harvested, cited.
`savills.co.uk` returns 403 to generic HTTP clients, which is why retrieval goes
through Gemini Search grounding rather than `httpx`.

## Tests

Four plain scripts, no runner, no API key, no network. 69 tests.
`for t in tests/test_*.py; do uv run python "$t"; done`

`test_time_axis` (19) the store and the time axis · `test_submarket_resolution`
(18) aliases resolve up, events match down · `test_sector_demand` (12) trailing
against leading · `test_watchlist_join` (20) lease windows, reversion, the E-4 trap.

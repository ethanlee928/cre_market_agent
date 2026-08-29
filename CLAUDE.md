# London Office Market Monitor

An AI agent that monitors the Central London office market for a commercial real
estate team. Opens with a ranked brief filtered to the buildings the user holds,
then answers follow-ups with sourced figures.

## Architecture: deterministic spine, agentic edges

This is the governing decision (design doc, Approach C). Hold the line on it.

**Plain Python — identical on every run, and defensible without the model:**

- `src/cre_agent/store.py` — fact store, period parsing, delta normalisation
- `src/cre_agent/signals.py` — detectors + severity ranking
- `src/cre_agent/watchlist.py` — asset loading, lease windows, relevance matching
- `src/cre_agent/submarkets.py` — the controlled vocabulary and its hierarchy
- `src/cre_agent/comps.py` — the peer matcher and the building-vs-building
  arithmetic (City Core and Canary Wharf rosters)
- Every figure rendered on the brief page, including the per-building reversion
  arithmetic, the peer-comparison card and both decision verbs

**Gemini 3.7 Flash — interpretation only:**

- Narrative for each signal, chat, news via Search grounding
- The agent narrates; it never computes. Every figure comes from a tool call.

## Non-negotiables

- **P3 — every number carries its source and as-of date.** An unsourced figure is
  a liability. "I don't have that" is a correct answer.
- **Empty watchlist must stay fully functional.** Market-wide is the base case;
  the watchlist is additive. Hard requirement, not a nicety.
- **A missing key degrades, it does not crash.** No longer advertised — the
  README asks for a key up front — but `Agent.enabled` and the guard in `ask()`
  stay. `genai.Client(api_key=None)` raises `ValueError` at construction, and
  `app.py` builds the Agent at module scope, so deleting the guard turns a
  missing key into an import-time crash that takes the brief down with it.
  Degrading is the floor, not a feature.
- **A watchlist figure is either sourced or the user's own, and labelled
  which.** The shipped portfolio is real — the five London holdings of Nan Fung
  Group via Endurance Land, every field public-sourced (the source named in
  each asset's note). What the public record does not carry is ABSENT, not
  invented: no passing rents, no lease dates. Do not "complete" the yaml with
  plausible tenancy data — the reversion and lease-window surfaces are designed
  to degrade, and `test_watchlist_join` pins that arithmetic with synthetic
  fixtures instead. The asset's own valuation comes from the store
  (`Comparison.asset_value_from_store`); a user-typed `rateable_value_psm` is
  only a fallback and renders as "(your figure)" — the label is under the most
  load in the peer table, where it sits beside VOA figures.
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
  `SYSTEM_PROMPT`: a closed tuple can be asserted, a prompt instruction can only
  be asked. "monitor" is not in the vocabulary and a test asserts it never
  appears.
- **£/m² never converts outside `comps.psm_to_psf`.** The VOA publishes per
  square metre; `gbp_psm` renders as £/m², and `PSF_PER_PSM = 0.09290304` is
  the one conversion site, pinned by test. An f-stringed conversion elsewhere
  is the bps trap again, ~11x wrong with no exception.
- **Peer comparisons run like for like, never across bases.** VOA valuations
  sit far below headline rents by construction (2024 fixed-date, net basis),
  so the asset's valuation compares with peer valuations and its passing rent
  with reported peer letting rents. `Comparison` holds no cross-basis field;
  a test asserts it never grows one.
- **A thin peer set refuses; it never averages.** Below `MIN_PEERS` (3
  buildings), `MIN_VALUATIONS` (3 levels) or `MIN_LETTINGS` (2 rents) the
  computed answer is the refusal sentence. Two buildings are an anecdote
  wearing a median's clothes.
- **Lease dates are `(year, month)` tuples, never `Period`.** `Period.parse
("2027-09")` raises, and `Period.parse("2026H2-2029")` discards the `H2`, so
  `contains()` answers True for Q1 of a window that opens in July. Use
  `watchlist.parse_ym`, and read window bounds off `Period.raw`.
- **Do not remove `include_server_side_tool_invocations=True`** from `tool_config`
  in `llm/gemini.py`. Mixing our function declarations with Google Search
  grounding in one request returns 400 without it.

## Entry point

One surface: `uv run streamlit run app.py`, or `make start` for that same app
in a container. There is deliberately no CLI — see the comment in
`pyproject.toml`.

**The container passes `--server.address=0.0.0.0` on the command line.** Committed
`.streamlit/config.toml` pins `address = "localhost"`, which inside a container
binds loopback only and makes `-p 8501:8501` publish a port nothing answers on. A
CLI flag outranks the config file, so the host default stays untouched. Do not
"fix" this by editing the config — binding localhost on the host is deliberate.

## Data

One seed file per source; `Store.load` globs `seed_*.json` and stamps each
file's `source` onto its facts. Top-level keys starting with `_` are loader-
ignored file commentary (harvest method, aggregation rule).

`data/seed_2026Q2.json` — Savills Central London Office Market Watch Q2 2026,
published 2026-08-06. 70 source records — 47 facts, 6 sector rows, 17 events —
which load as 53 Facts and 17 events (sector rows normalise into Facts; a Fact
treats `sector` as part of its identity). Real, harvested, cited.
`savills.co.uk` returns 403 to generic HTTP clients, which is why retrieval goes
through Gemini Search grounding rather than `httpx`.

`data/seed_voa_cw_2026list.json` — VOA 2026 rating list (antecedent valuation
date 2024-04-01): 15 building-level `rateable_value_avg` facts in `gbp_psm`,
each the aggregate RV over aggregate floor area across that building's office
hereditaments in the E14 slice, hereditament count in `extras`. Real, computed
from the bulk download; the aggregation script is not kept, the method note in
the file is.

`data/seed_cw_buildings_2026.json` — the Canary Wharf roster: 18 real buildings
(name, sqft, year_built, floors, whole-building EPC where the register holds
one), per-row provenance in notes. Identity and physical constants only;
anything measured about a building is a Fact carrying `building`.

`data/seed_colliers_cw_jan2026.json` and `data/seed_carterjonas_cw_2026Q1.json`
— the Canary Wharf aggregates: vacancy 8.3% at **2025Q4** (Colliers Jan 2026 —
no open source publishes a 2026 numeral; a fresher figure exists only inside
CoStar) and prime rent £57.50 at 2026Q1 (Carter Jonas — a search snippet
mis-attributed both figures before verification; the `_method` notes record
the correction). `data/seed_cw_press_events_2026.json` — three building-level
events (HSBC vacating 8 Canada Square, Citi refurbishing 25 Canada Square,
PwC under offer at Eden), citations per event note.

`data/seed_voa_city_2026list.json` — same VOA list, City slice: 32 building-level
`rateable_value_avg` facts for the Cheapside/Gresham/Wood, Old Bailey/Ludgate/
Fleet Place and Cannon Street corridors, three of them the holdings themselves.
138 Cheapside (Cheapside House) needed a hand-written matcher — the VOA names
its floors three ways and trailing-number matching finds 2 of its 12
hereditaments. 99 City Road has no assessment while stripped for redevelopment;
that absence is data. `data/seed_city_buildings_2026.json` — the City Core
roster: 32 real buildings, per-row provenance, register-verified whole-building
EPCs where they exist. `data/seed_endurance_events_2026.json` — seven events on
the Nan Fung holdings (OpenAI's 88,500 sq ft at Regent Quarter, the £300m
99 City Road retrofit start, lettings), citations per note; the unverified
Green Street "£140m Holborn trophy" headline is deliberately a `_method` note,
not an event.

`config/watchlist.yaml` — the real portfolio: five Nan Fung Group London
holdings via Endurance Land (enduranceland.com crawl 2026-08-29, verified
against nanfung.com and trade press). Rent-roll fields deliberately absent.

## Tests

Six plain scripts, no runner, no API key, no network. 143 tests.
`for t in tests/test_*.py; do uv run python "$t"; done`

`test_time_axis` (27) the store and the time axis, that a detector
omits an unpublished extra rather than printing it as zero, and E-10 source
precedence (the newer `published` wins an identity collision, from either side
of the filename sort) · `test_submarket_resolution`
(18) aliases resolve up, events match down · `test_sector_demand` (12) trailing
against leading · `test_watchlist_join` (30) lease windows, reversion (synthetic
fixtures — the real portfolio carries no rent roll), the E-4 trap, that a mistyped yaml key names itself
rather than raising a bare TypeError, and that the
coverage table resolves every claim it makes · `test_peer_comps`
(29) the peer matcher's bands and reasons, the three refusal floors, the pinned
£/m² constant, like-for-like verdicts, the own-valuation-from-store rule,
self-exclusion from peer sets, and that chat and brief share one code
path · `test_agent_loop` (27)
`ask()` driven by a fake client returning real `google.genai.types` objects — the
final answer enters history, MAX_TURNS reports instead of looping, the missing-key
guard degrades (automated; the hand check that used to live here is now
`test_missing_key_yields_error_not_traceback`) — plus `_run_tool` dispatch, the
tool-argument trust boundary (an undeclared filter and a bogus sector name each
name themselves rather than failing as a raw TypeError or a false "no data"), and
the eval graders.

## Evals

`uv run python evals/run.py [--n 3] [--case id]` — 33 live cases against the real
model, measuring the half a test cannot pin: every figure in an answer must trace
to tool traffic (or carry a web citation), macro questions must refuse or cite,
decisions close on `ACTIONS` and never "monitor". Needs `GOOGLE_API_KEY`; `tests/`
stays key-free on purpose. A case fails the run only when it fails a majority of
its reps. Transcripts land in `evals/runs/` (gitignored) for diffing across prompt
or model changes. Run it after touching `SYSTEM_PROMPT`, the tool declarations,
or `CRE_MODEL`.

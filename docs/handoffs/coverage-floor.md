# Handoff: Buy the coverage floor cheaply

**Status:** not started
**Estimated effort:** ~3 hours
**Owner:** unassigned
**Prereq:** none — all three tasks are independent of each other

---

## Why this exists

The task brief this project answers names eight key areas:

1. Prime and Grade A office rents
2. Vacancy / availability rates
3. Leasing take-up / activity
4. Supply pipelines (new developments, refurbishments, pre-lets)
5. Submarket dynamics (City, West End, Canary Wharf, Midtown/Fringe)
6. Macroeconomic influences (interest rates, economic indicators, employment)
7. Occupier demand drivers (flight-to-quality, ESG, hybrid working)
8. Emerging news / events

A current audit of the codebase against those eight:

| # | Area | Data | Detector | Status |
|---|---|---|---|---|
| 1 | Prime / Grade A rents | strong | `quality_spread` | OK |
| 2 | Vacancy / availability | 8 submarkets | **missing** | gap |
| 3 | Take-up / activity | strongest in seed | none | acceptable |
| 4 | Supply pipeline | strong | `supply_shock` | OK |
| 5 | Submarket dynamics | uneven | **missing** | gap |
| 6 | Macro | **none** | none | **hole** |
| 7 | Occupier drivers | FTQ + ESG yes, hybrid none | partial | acceptable |
| 8 | News / events | grounding wired | n/a | acceptable |

**The distinction that governs this work: coverage is not prominence.**

Chat must be able to answer a question in all eight areas — or say honestly that
it cannot. That is the *floor*, and it is what this document buys.

The ranked brief on the front page deliberately surfaces only ~4 signals. Do not
add detectors to "cover" areas 3, 7 or 8. Eight equal tiles is a dashboard, which
is the thing this product replaces.

Spend a maximum of ~3 hours here. The remaining time belongs to
`docs/handoffs/watchlist-join.md`, which is where the actual product value is.

---

## Current state of the code

```
src/cre_agent/store.py       342 lines  Fact store. Period parsing, delta
                                        normalisation, ambiguity refusal.
src/cre_agent/signals.py     182 lines  3 detectors + registry + Signal model.
src/cre_agent/watchlist.py   137 lines  Asset, Watchlist, SubmarketIndex.
src/cre_agent/llm/gemini.py  267 lines  4 tool declarations, streaming Agent.
config/submarkets.yaml                  Controlled vocabulary + hierarchy.
config/watchlist.yaml                   3 fictional illustrative assets.
data/seed_2026Q2.json                   47 facts, 17 events, 6 sector rows.
```

There is **no `cli.py` and no `app.py`**, although `pyproject.toml` declares
`cre = "cre_agent.cli:main"`. That entrypoint is a separate and higher-priority
piece of work; nothing in this document is gradeable until it exists.

Key APIs you will use:

```python
store.get(metric, submarket, period=None, sector=None) -> Fact | None
store.find(metric=None, submarket=None, period=None, sector="__any__") -> list[Fact]
store.submarkets() -> list[str]
store.metrics() -> list[str]
store.as_of() -> str          # newest source publication date

fact.value          # float | None  — may be None with a delta present
fact.unit           # pct | bps | sqft | gbp | gbp_psf | count
fact.render_value() # unit-aware string
fact.delta(kind, basis=None) -> Delta | None   # kind: qoq yoy ytd vs_avg forecast
fact.source.cite()

Signal(id, severity, headline, detail, evidence: list[Fact], affected: list[str])
# severity is one of RISK / WATCH / OPPORTUNITY (module constants)
```

---

## Task 1 — `submarket_divergence` detector (~1 hour)

Fills areas 2 and 5. The data already exists; three sibling detectors already
establish the pattern. This is the cheapest coverage in the project.

**File:** `src/cre_agent/signals.py`

**Spec**

Compare each submarket's `vacancy_rate` against the Central London benchmark
(7.2% in 2026Q2) and against its own 10-year average where the seed publishes
a `vs_avg` delta.

Available vacancy facts:

| Submarket | Value | vs 10yr avg |
|---|---|---|
| Central London | 7.2% | +80 bps (long run) |
| City | 7.0% | +10 bps |
| City Core | 5.9% | −200 bps |
| City Fringe | 8.0% | +150 bps |
| West End | **null** | +190 bps |
| West End Core (Mayfair/St James's) | 4.4% | −70 bps |
| Hammersmith | 22.0% | — |
| VNEB | 18.0% | — |

Emit a Signal per submarket that diverges materially. Suggested thresholds,
tune on the real output:

- `RISK` — vacancy is >150 bps above the Central London benchmark, **or** the
  `vs_avg` delta is >+150 bps
- `OPPORTUNITY` — vacancy is >100 bps below the benchmark **and** the `vs_avg`
  delta is negative (tight market, pricing power for a landlord)
- otherwise emit nothing

**Non-negotiable gotchas**

- `West End` has `value: None` with deltas present (seed defect E-4). Read the
  delta, never assume a level. `quality_spread` already handles exactly this
  case — copy its approach.
- Do not print bps as pct. Use `Delta.render()`, never f-string the raw number.
- Cap output at 3 signals. Hammersmith at 22% and VNEB at 18% will both fire
  and neither is Central London core; rank by absolute divergence and truncate.
- Call `_match(watchlist, submarket=...)` so `affected` is populated. The
  fictional watchlist holds Clerkenwell Works in City Fringe, so the City
  Fringe signal should come back with an affected asset. If it does not, the
  submarket hierarchy lookup is broken — fix that, do not work around it.
- Register in the `DETECTORS` list at the bottom of the module.

**Acceptance**

- `detect_all(store, watchlist)` returns a City Fringe RISK signal naming
  Clerkenwell Works, and a City Core or West End Core OPPORTUNITY signal.
- A unit test in `tests/` asserts the exact headline string for one signal.
- No signal renders a bps value with a `%` sign.

---

## Task 2 — minimal `macro_context` skill (~2 hours)

Area 6 is the only one of the eight with **no path to any answer**. It is also
the only skill that reaches outside the cached seed, which makes it the clearest
on-screen proof that this is an agent rather than a report renderer.

**Files:** new `src/cre_agent/skills/macro.py`, plus a tool declaration and
dispatch branch in `src/cre_agent/llm/gemini.py`.

**Scope — deliberately small**

Three numbers, fetched live, no history, no charts:

1. Bank of England Bank Rate
2. UK 10-year gilt yield
3. ONS employment or CPI — whichever is cheapest to retrieve reliably

**Implementation note**

Google Search grounding is already wired and verified to coexist with the
function declarations (`tool_config` carries
`include_server_side_tool_invocations=True` — see the module docstring in
`gemini.py`; do not remove that flag). Two viable routes:

- **Simplest:** a `get_macro()` tool whose implementation issues a grounded
  search and returns the parsed values with their grounding citations.
- **More robust:** hit the BoE / ONS statistical endpoints directly with the
  `httpx` dependency already in `pyproject.toml`, and fall back to grounding.

Take the simplest route first. Robustness is a stretch goal.

**Provenance requirement — do not skip**

`Fact.provenance` already distinguishes `seed | grounded | model`. Macro values
are `grounded`. They must render on screen visibly differently from seed facts,
because a live grounded number is a weaker evidence class than a dated Savills
publication. The system prompt's rule — every figure comes from a tool call,
never from recall — applies unchanged.

**The payoff to wire up**

Once Bank Rate and the 10-year gilt exist, the **yield-versus-gilt spread**
becomes computable: `prime_yield` (City and West End, already in the seed) minus
the 10-year gilt. That spread is the risk premium, and it is the number that
explains why investment turnover is 35% below the 10-year average. Surface it in
the macro answer even if no detector consumes it yet.

**Acceptance**

- Asking the chat "what's the Bank Rate?" returns a number with a source, not a
  refusal and not a recalled figure.
- The tool call appears in the streamed event log like any other.
- With no network, the tool fails gracefully and the agent says it cannot reach
  live data — it does not invent a rate.

---

## Task 3 — coverage panel (~30 minutes)

The highest value-per-minute item in this document.

**What**

One view — a CLI subcommand, a sidebar panel, or a slide-ready table — mapping
the brief's eight key areas to what the system actually holds:

```
KEY AREA                     DATA              SURFACED BY          STATUS
1 Prime / Grade A rents      Savills Q2 2026   quality_spread       covered
2 Vacancy / availability     8 submarkets      submarket_divergence covered
3 Take-up / activity         4 series, 6 sect  chat only            on request
4 Supply pipeline            6 metrics         supply_shock         covered
5 Submarket dynamics         8 submarkets      submarket_divergence partial —
                                                no Canary Wharf, no Midtown
6 Macro                      live lookup       macro_context        live only,
                                                no history
7 Occupier drivers           FTQ + ESG         quality_spread       partial —
                                                no hybrid-working data in source
8 News / events              17 seed events    search grounding     on request
```

**Mark the gaps honestly.** "Hybrid working: not published by Savills" scores
better with a reviewer than a fabricated proxy, and it is the same discipline as
the project's P3 premise (every number carries its source; the agent says what
it does not know).

**Acceptance**

- Every one of the eight areas appears with a status.
- At least two are marked partial or gap.
- Nothing in the table is aspirational — it reflects what is wired at that
  moment, not what is planned.

---

## Out of scope

Do not build any of these under this document:

- Charts, time series, back-quarter harvests
- A named `news_scan` skill wrapper — grounding already works; just label the
  tool call in the stream
- A second data source
- Detectors for areas 3, 7 or 8
- An Anthropic model adapter

---

## Definition of done

1. Four detectors registered; `submarket_divergence` fires on real seed data.
2. Chat answers a macro question with a live, cited number.
3. Coverage panel renders all eight areas with honest statuses.
4. No new failing tests; the deterministic spine still returns identical output
   across two consecutive runs.

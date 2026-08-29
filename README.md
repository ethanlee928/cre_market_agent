# 🏢 London Office Market Monitor

An AI agent that monitors the Central London office market for a commercial real
estate team. It opens with a briefing of what moved this quarter, filtered to the
buildings you actually hold, then answers follow-up questions with sourced figures.

Built on **Gemini 3.7 Flash** with function calling and Google Search grounding.

---

## Quick start

**Prerequisites:** Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). If you don't have `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**1. Install dependencies**

```bash
uv sync
```

**2. Add your API key**

```bash
cp .env.example .env
```

Open `.env` and paste a Google AI Studio key into `GOOGLE_API_KEY`. Free keys are
available at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

**3. Start the server**

```bash
uv run streamlit run app.py
```

The app opens at **http://localhost:8501**. Press `Ctrl+C` in the terminal to stop it.

### Or in Docker

If you would rather not install Python and `uv` at all, `make start` builds an image
and serves the same app on the same port:

```bash
make build          # build the image
make start          # build, then run detached
make stop           # shut it down
```

The image builds on the pinned
[`uv` base image](https://github.com/astral-sh/uv-docker-example), installs from
`uv.lock` so the container resolves the wheels your laptop resolved, and runs as a
non-root user. Your key is passed in at run time from `.env` and never enters an
image layer — and with no `.env` at all the container still serves the full brief,
with chat switched off.

## Scope

A self-contained Python proof of concept, judged on one thing: the agent loop and
the tool calls under it. One command, one process, nothing to stand up. What that
bought, and what it cost.

**Chosen, with reasons**

- **No RAG, no vector store.** Figures live in a structured fact store
  (`src/cre_agent/store.py`) and reach the model through function calls. Chunk and
  embed the source PDF instead and a reversion stops being computable, an as-of
  date stops travelling with the number it belongs to, and an unpublished level
  gets a plausible one invented for it. The store is the reason every figure on
  screen can be traced; retrieval over prose would have made that promise
  unkeepable.
- **No API tier, no MCP server, no SPA.** Streamlit is the entire presentation
  layer and the agent runs in the same process — one entry point,
  `uv run streamlit run app.py`. A transport boundary would have added deployment
  surface and proved nothing about the loop.
- **No database server.** Everything loads from `data/seed_*.json` at startup —
  one file per source, 102 Facts and 27 events across nine sources. Read-only,
  in memory.
- **No observability stack** — no Langfuse, no tracing backend. Every tool call is
  expanded in the UI directly above the answer it produced, which at this scale
  serves the same purpose: you can see which figures were looked up, and which the
  agent declined to invent.
- **One primary market provider, plus open data.** Savills, Central London
  Office Market Watch Q2 2026, is the market spine; the VOA rating list, the
  EPC register, and Colliers/Carter Jonas/trade-press figures join it one seed
  file per source. Merging providers is a mapping problem rather than a loading
  problem — definitions and submarket boundaries differ — which is what
  `src/cre_agent/submarkets.py` is for: a controlled vocabulary with an explicit
  hierarchy, so an alias resolves upward to the node that actually holds facts.
  The same reasoning is why the sector vocabulary was dropped rather than guessed.
  Savills uses different cuts in different tables and never claims they are
  equivalent, and `Insurance & Financial` is not `Financial & Banking`.
- **A fixed strategy vocabulary.** Six decision verbs — `regear`, `refurbish`,
  `re-price`, `hold`, `defer capex`, `start the conversation` — declared in
  `src/cre_agent/signals.py`. They are assumed, not derived; a real portfolio team
  would own that list. What matters is that it is closed and built in Python, so
  a test can assert that "monitor" — the verb that lets a paragraph end without
  deciding anything — never appears. Held in the prompt instead, that could only
  be asked for.

**Not built, stated plainly rather than discovered**

- No automated data ingestion. The quarterly dataset was harvested once, by hand,
  by reading the published article and transcribing the figures. All 70 records
  were later re-checked against the source; see **Data and attribution**.
- One quarter of history, so trends are quoted from the source rather than computed.
  Q1 2026 is the obvious next harvest and would make quarter-on-quarter computed.
- Tests cover the store, the time axis, the detectors and the agent loop — the
  loop deterministically, against a fake client returning real SDK objects. The
  model's live behaviour is measured separately by `evals/` (see **Verifying
  your setup**). Only the Streamlit layer is exercised by hand.
- No authentication, no multi-user, no deployment pipeline. Local run only.

## What you should see

On load, a briefing with seven ranked signals:

|                | Signal                                                                                             | Your exposure                                        |
| -------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 🔴 RISK        | The Bailey valued 18% above its 14-peer street (£617 against £525 per m²)                          | The Bailey                                           |
| 🟠 WATCH       | 108 Cannon Street valued in line with its 3-peer street (£584 against £574 per m²)                 | 108 Cannon Street                                    |
| 🟢 OPPORTUNITY | 41 large requirements chasing 21 options (2.0:1)                                                   | The Bailey, 99 City Road, Regent Quarter             |
| 🔴 RISK        | City Grade B rents falling while Grade A rises (−2.6% against +7.0%)                               | —                                                    |
| 🔴 RISK        | West End Grade B rents falling while Grade A rises (−11.3% against +4.0%)                          | —                                                    |
| 🟠 WATCH       | Record 7,700,000 sq ft of completions in 2026, only 35% pre-let                                    | —                                                    |
| 🟢 OPPORTUNITY | Insurance & Financial take-up at a series low, yet 41% of space under offer against 20% of take-up | —                                                    |

The sidebar shows the portfolio — the five real London holdings of Nan Fung
Group, extracted from its development platform Endurance Land's public
portfolio page — each tagged with the worst signal touching it. Signals that
hit your holdings sort to the top and expand by default.

### Building vs building — the peer card

The lead signal is a peer comparison in which **every figure on both sides is
real**: The Bailey, the holding at 16 Old Bailey, against fourteen named City
buildings of similar size and vintage — 20 Old Bailey's neighbours across the
Cheapside, Gresham/Wood Street, Ludgate/Fleet Place and Cannon Street
corridors — each with its floor area, completion year, whole-building EPC
where the register holds one, and a valuation computed from the VOA 2026
rating list (aggregate rateable value over aggregate office floor area, £/m²,
on the 2024 antecedent valuation date). The asset's own £/m² comes from the
same VOA list as its peers', so the 18% gap is one public dataset disagreeing
with itself about one street — nothing on the card is invented or estimated.
The verdict is computed in Python, like for like: valuation against valuation,
never a passing rent against a valuation — the two sit on different bases by
construction.

The refusals are as deliberate as the cards. 138 Cheapside is 1958 stock the
±10-year age band finds no street for, so its comparison refuses rather than
stretches; 99 City Road is stripped for redevelopment and holds no 2026
rating-list assessment; Regent Quarter is an estate of twelve buildings, not a
building. Ask about any of them and the answer is the computed refusal with
its reason — never peers assembled from memory.

### The join, and the rung it deliberately leaves empty

Each matched building gets its own sentence, computed in Python and closing on a
decision verb — `regear`, `refurbish`, `re-price`, `hold`, `defer capex`,
`start the conversation`. Never "monitor": that is the word that lets a paragraph end
without deciding anything.

The rung that stays empty on the shipped brief is the rent roll. The five
holdings are real, and their passing rents, breaks and expiries are exactly
the data no public source carries — so the yaml omits them rather than
inventing them, and the reversion column and lease-window matching degrade
the way an empty watchlist always has. Type your own rents into
`config/watchlist.yaml` and this is what lights up, per building (the
arithmetic is pinned by tests against synthetic fixtures):

> **Clerkenwell Works** — passing £52.00 psf against the City Grade B average of £45.66 psf
> (2026H1), which is £240,920 a year above market across 38,000 sq ft, exposed at the next
> review; reaching the City Grade A average of £76.21 psf would be worth £919,980 a year
> gross, before capex and voids.
> → **regear**

> **Mayfair House** — no reversion figure is computable: Savills publishes the West End
> Grade B change (−11.3%) but not the level, so there is nothing to measure the passing
> rent against; reaching the West End Grade A average of £106.17 psf would be worth
> £580,080 a year gross, before capex and voids; EPC D is below the standard several of
> those proposals would require, which comes first.
> → **defer capex**

Two buildings, the same ladder, one missing rung — and the app says which rung and what
it costs rather than substituting a number. The two figures per building are
deliberately two and not three: passing-minus-Grade-B and Grade-A-minus-passing sum
exactly to the grade gap, so printing all three invites a reader to add two of them and
count the same spread twice.

Then ask it something:

> **Should we be worried about the City Fringe?**
>
> Yes, particularly for development exposure. City Fringe vacancy stands at 8.0%, which is
> 150 bps above its long-run average _(Savills, Q2 2026, as of 2026-08-06)_. Your asset
> 99 City Road sits in this submarket — a 475,000 sq ft retrofit with main works starting
> Q3 2026 and completion targeted 2030...

Or ask who is actually active:

> **Who's taking space right now?**
>
> Anthropic took 158,138 sq ft at 1 Triton Square, NW1, OpenAI acquired 77,521 sq ft
> at Jahn Court, 34 York Way, N1, and Fractile AI signed for 21,734 sq ft at the
> JJ Mack Building, EC1 at £115.00 psf _(Savills, Q2 2026)_...

Expand the tool-call panel above any answer to see exactly which figures the agent
looked up. Every number it states comes from a tool call, never from memory.

---

## North stars

Three analyst workflows the system is built to serve, written as acceptance tests.
Each query has an expected result, and **a refusal counts as a pass** wherever the
data genuinely does not exist. An unsourced answer is the only real failure.

Goal 3 leads: it is the only one whose conclusion is not already stated in the
source, so it is the one that proves this is not a PDF summariser.

|     | Meaning                                                     |
| --- | ----------------------------------------------------------- |
| ✅  | Answers with sourced figures                                |
| ◐   | Answers, with a stated limitation                           |
| ⚠️  | Known defect — answers, but wrongly                         |
| ⛔  | No data. "I don't have that" is the correct, passing answer |

### 1. Morning briefing — macro and event intelligence

_Catch overnight moves and named deals before a client or IC meeting._

| Test query                                                                      | Today                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Which occupiers signed over 30,000 sq ft?"                                     | ✅ Nine lettings, largest first, each cited                                                                                                                                                                                                                                                             |
| "…in the City Core or West End specifically"                                    | ◐ Anthropic's 158,138 sq ft now surfaces: `find_market_activity` takes a `submarket`, and matching walks the hierarchy, so a West End question reaches a deal filed under North of Oxford Street East. 13 of 27 events carry no submarket, so the answer states that the count is a floor |
| "Flag Bank Rate, gilt yields, rate shifts"                                      | ⛔ No macro data, no `macro_context` tool. Should refuse; today it may answer freehand from Search grounding without labelling the figure as live                                                                                                                                                       |
| "…this week"                                                                    | ⛔ Source is quarterly, published 2026-08-06. Not answerable from the store at any granularity                                                                                                                                                                                                          |
| "New planning applications over 100,000 sq ft, with developers and ESG targets" | ⛔ No planning-stage data. `development_starts` is construction stage, downstream of planning                                                                                                                                                                                                           |

### 2. Pre-underwriting — micro-market divergence

_Compare submarket benchmarks without reading a 50-page PDF._

| Test query                                                         | Today                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "City Core versus West End Core vacancy"                           | ✅ 5.9% and 4.4%, both against their 10-year averages                                                                                                                                                                                                                                |
| "Compare Canary Wharf, City Core and Mayfair/St James's"           | ✅ `Mayfair` resolves — 4.4%, and the answer names the node it came from. Canary Wharf answers at its own node since the estate harvest: vacancy 8.3% (Colliers, 2025Q4) and prime rent £57.50 (Carter Jonas, 2026Q1), each carrying its true, older as-of date |
| "Pre-let pipeline ratios by submarket"                             | ◐ `prelet_pct` exists only at Central London level — 35% forecast, 25% under construction, 18% to 2029. No submarket breakdown published                                                                                                                                             |
| "Southbank comps, rent-free months, headline versus net effective" | ⛔ Southbank holds zero facts. Rent-free incentives and net effective rent are not metrics in this source, and net effective is not derivable without them                                                                                                                           |

### 3. Tenant risk and sector demand — occupier signals

_Target leasing campaigns, read corporate footprint changes._

The richest data in the store, and currently the least reachable.

| Test query                                              | Today                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Which sectors are expanding?"                          | ✅ Financial \& Banking 5.8m sq ft (+30% YTD), Professional Services 3.58m (+23%), Tech \& Media 3.1m (+26%) — all records, each cited. `get_metric` takes a `sector`, and `list_available` advertises only names it can actually fetch                                                                                                                               |
| "Are occupiers growing or shrinking?"                   | ◐ `expanding_occupier_share` is reachable — 43% expanding against 12% contracting, 15% new entrants — but no detector surfaces it on the brief                                                                                                                                                                                                                        |
| "Which sector should we target for a leasing campaign?" | ✅ `sector_demand` computes it and it leads the answer: Insurance \& Financial take-up at 878,112 sq ft, −21% on the five-year average and the weakest in the series, while the sector holds **41% of space under offer against 20% of take-up** — twice its completed weight. Trailing and leading disagree; the leading number is the one that has not happened yet |
| "Will our tenant renew?"                                | ⛔ No tenant-level data, and prediction is out of scope for a sourced system. The honest answer is the market base rate: 1.4m sq ft of the 3.1m sq ft under offer is regear, 93% of it from occupiers already in 50,000 sq ft or more                                                                                                                                 |

Goal 3 closed with two of the three things planned, and deliberately without the
third. `get_metric` took a `sector` parameter, and `sector_demand` now fires on
trailing take-up disagreeing with the leading pipeline.

The sector vocabulary was dropped on purpose. Joining `Insurance & Financial` to
`Financial & Banking` would have asserted that two Savills groupings are the same
sector, and insurance is not banking — the source uses different cuts in different
tables and never claims they are equivalent. The stronger signal needs no such
inference: it compares one sector against _itself_, take-up versus space under
offer, which is a comparison the source fully supports and never draws.

One figure had to move to make it computable. "41% of space currently under offer"
lived inside a note string, and a detector parsing prose for a number is the exact
defect this store exists to prevent. It is now `share_of_under_offer_pct` in the
fact's `extras`, declared in `EXTRA_FIELDS`, with the original sentence kept for
provenance. Same defect class as the sector rows that were unreachable before.

---

## Coverage of the brief's nine key areas

```bash
uv run python scripts/coverage.py
```

```
+----+----------------------------------+--------------------+----------------------------+-------------+
| #  | KEY AREA                         | DATA               | SURFACED BY                | STATUS      |
+----+----------------------------------+--------------------+----------------------------+-------------+
| 1  | Prime / Grade A rents            | 4 of 4 metrics     | quality_spread             | covered     |
| 2  | Vacancy / availability           | 5 of 5 metrics     | large_occupier_squeeze     | covered     |
| 3  | Take-up / activity               | 4 of 4 metrics     | sector_demand              | covered     |
| 4  | Supply pipeline                  | 5 of 5 metrics     | supply_shock               | covered     |
| 5  | Submarket dynamics               | 2 of 2 metrics     | chat only                  | on request  |
|     no Midtown or Southbank facts in any loaded source; a query for one answers at the nearest        |
|     published parent and says so. Canary Wharf publishes via the Colliers (vacancy, 2025Q4) and       |
|     Carter Jonas (prime rent, 2026Q1) seeds                                                           |
| 6  | Macro (rates, gilts, Bank Rate)  | none in source     | not surfaced               | gap         |
|     not in a quarterly agency report; no tool, and the model is instructed to refuse rather than      |
|     answer from memory                                                                                |
| 7  | Occupier drivers                 | 4 of 4 metrics     | quality_spread             | partial     |
|     flight-to-quality and ESG are covered; no hybrid-working data is published in this source         |
| 8  | News / named events              | 27 events          | chat only                  | on request  |
|     27 seed events (17 Savills transactions, 3 Canary Wharf building events, 7 on the Nan Fung        |
|     holdings) plus live Search grounding; no detector, and 13 of the 27 carry no submarket            |
| 9  | Peer comparison (building vs bui | 1 of 1 metrics     | peer_gap                   | partial     |
|     rosters for the City Core corridors and Canary Wharf only; valuations are VOA rateable values     |
|     on a fixed 2024 basis, not passing rents; building-level vacancy limited to individually          |
|     reported majors                                                                                   |
+----+----------------------------------+--------------------+----------------------------+-------------+
  4 covered · 2 partial · 2 on request · 1 gap
  Computed from 102 facts, 27 events and 5 detectors at render time.
```

Nothing in that table is typed in. Only the area names, their metric lists and the
caveats are declared; the data column, the detector column and the status are derived
from the fact store and the detector registry at render time, so renaming a detector
drops its area to `on request` rather than leaving a stale claim of coverage.

Its own limit, stated rather than discovered: this proves an area is _wired_ — that a
declared metric is in the store and a declared detector name resolves to a function that
produces output. It does not prove the wiring is good.

**Macro reads as a gap on purpose.** A gilt-yield lookup was scoped and cut. It is the
one figure in the product that would have arrived from a live web fetch rather than a
published line, in a system whose entire argument is that every number can be traced —
so an honest empty row is worth more here than a number with a weaker provenance than
everything beside it.

---

## Verifying your setup

Two diagnostics, neither required for normal use:

```bash
uv run python scripts/probe_models.py
```

Lists every Gemini model your key can reach. Use it if you get a model-not-found error,
then set `CRE_MODEL` in `.env` to one that appears.

```bash
uv run python scripts/smoke_test.py
```

Four checks: plain generation, function calling, Search grounding, and the two combined
in a single request. All four should pass.

```bash
for t in tests/test_*.py; do uv run python "$t"; done
```

123 tests across six files. None needs an API key, a network or a test runner — they
are plain scripts, so there is nothing to install. Most guard failures that only appear
once a second quarter is merged: periods sorting by their string name, a metric changing
grain between reports, a detector subtracting two figures from different years. One file
guards the join — that a lease window opening in July 2026 rejects a March 2026 break,
and that a `Fact` carrying a `None` level never has a comparison built against it. The
newest, `test_agent_loop.py`, drives `ask()` against a fake client returning real
`google.genai.types` objects: the final answer enters conversation history, tool rounds
round-trip in order, and a missing key degrades to a friendly error instead of a
traceback.

### Live evals

```bash
uv run python evals/run.py --n 3
```

The deterministic half is proven by tests; the model's half can only be measured. 33
canned questions run against the live model, graded programmatically: every figure in
an answer must appear in the tool traffic that produced it (or carry a web citation
and say so), macro questions must refuse or answer from the web labelled as such,
decision questions must close on the fixed verb vocabulary and never on "monitor",
and the two multi-turn cases hold the conversation-history round-trip against the
real API. Needs `GOOGLE_API_KEY`. A case fails the run only when it fails a majority
of its reps, so one flaky rep does not cry regression and a real one cannot hide.
Full transcripts land in `evals/runs/` for diffing after any prompt or model change.

## Configuration

All in `.env`:

| Variable         | Default            | Purpose                                                |
| ---------------- | ------------------ | ------------------------------------------------------ |
| `GOOGLE_API_KEY` | _(none)_           | **Required.** Missing, chat is disabled rather than crashing |
| `CRE_MODEL`      | `gemini-3.7-flash` | Swap models without touching code                      |
| `CRE_THINKING`   | `medium`           | `low`, `medium` or `high`. Lower is faster and cheaper |

Your portfolio lives in `config/watchlist.yaml`. The shipped one is real — the
five London holdings of Nan Fung Group, held through Endurance Land, every
field public-sourced and its rent roll deliberately absent. Edit it (add your
own rents and lease dates to light up the reversion column), or delete every
asset: the market-wide view works completely with an empty watchlist.

## Data and attribution

Market figures come from **Savills, Central London Office Market Watch Q2 2026**,
published 6 August 2026, reproduced here for a non-commercial proof of concept;
from the **VOA 2026 non-domestic rating list** (open data, aggregated to
building level for Canary Wharf and the City Core corridors — the method notes
travel inside `data/seed_voa_cw_2026list.json` and
`data/seed_voa_city_2026list.json`); and from the **non-domestic EPC register**
and named public building records for both rosters, cited row by row in
`data/seed_cw_buildings_2026.json` and `data/seed_city_buildings_2026.json`.
Attribution appears in the app beside every figure.

The portfolio in `config/watchlist.yaml` is **real**: the five London holdings
of Nan Fung Group, extracted from the public portfolio of Endurance Land (its
wholly-owned development platform since August 2024) and verified against
nanfung.com and 2024–26 trade press — 138 Cheapside, 108 Cannon Street, The
Bailey, 99 City Road and Regent Quarter. Every field carries a public source,
named in each asset's note; the rent roll (passing rents, breaks, expiries) is
not public and is deliberately absent rather than invented. Publicly reported
lettings on the holdings — OpenAI's 88,500 sq ft at Regent Quarter among them —
are cited events in `data/seed_endurance_events_2026.json`.

Live web results come from Google Search grounding and are labelled separately from
stored figures, because they carry different confidence.

**Provenance, stated plainly:** savills.co.uk returns HTTP 403 to ordinary HTTP
clients, so the article was read in a browser and the figures transcribed by hand
into `data/seed_2026Q2.json`. There is no scraper to inspect. On 29 August 2026 all
70 source records — 47 facts, 6 sector rows, 17 named transactions — were re-read
against the source; no discrepancies were found. Those 70 load as 53 Facts and 17
events (the 6 sector rows normalise into ordinary Facts rather than a parallel
model); the sidebar's larger count adds the Canary Wharf and City files below. One field is
an inference rather than a quote: Anthropic's letting at 1 Triton Square is tagged
to the North of Oxford Street East submarket, which is correct but is not stated
in the article.

**Provenance of the Canary Wharf building data** (harvested 29 August 2026): the
VOA figures were computed from the official bulk download — two zips, 88 MB of
rating-list entries and 148 MB of summary valuations (735 MB unzipped, ~2 million
hereditaments for England and Wales, asterisk-delimited). That national file was
filtered to the 5,870 E14 hereditaments, matched to buildings by street name plus
street number, restricted to office descriptions, and aggregated to one figure
per building: aggregate rateable value over aggregate floor area, in £/m² —
area-weighted because serviced-office operators fragment buildings into hundreds
of micro-suites that would dominate an unweighted median. The 15 resulting facts
in `data/seed_voa_cw_2026list.json` carry the underlying totals and hereditament
counts in their notes, and the file's `_method` key states the rule; the raw
download was deleted after aggregation and is repeatable from the URL in the
source block. Three buildings had no matchable office assessment and carry no
figure. EPC ratings were read building by building from the public
find-energy-certificate register (certificate numbers in the roster notes);
roster sizes and ages come from the public records each row cites; the Colliers
and Carter Jonas aggregates were verified against the publishers' own PDFs —
which corrected two mis-attributions a search snippet had suggested, recorded in
those files' `_method` notes. The fuller narrative, including what the harvest
changed in the design, is recorded in the maintainers' design notes (kept
outside the repository).

**Provenance of the City building data and the portfolio** (harvested 29 August
2026): the same VOA bulk download, re-fetched and sliced to the Cheapside/
Gresham/Wood Street, Old Bailey/Ludgate/Fleet Place and Cannon Street
corridors, aggregated by the same area-weighted rule into the 32 building
figures of `data/seed_voa_city_2026list.json` — three of which are the
holdings themselves, so both sides of the peer card come from one list. Two
matching traps are recorded in that file's `_method`: Cheapside House's floors
are named three different ways (naive matching finds 2 of its 12
hereditaments), and 99 City Road has no assessment at all while stripped for
redevelopment — an absence kept as data. The portfolio itself was extracted
from `enduranceland.com/portfolio` (crawled the same day, filtered to
investment partner = Nan Fung Group), cross-checked against nanfung.com's
property pages — which also settled 108 Cannon Street's true size at 38,800
sq ft, within 1.2% of the VOA aggregate — and against trade press for 2024–26
status: all five holdings verified held, with one unresolved signal (a
paywalled September 2025 Green Street headline about a marketed "£140m Holborn
trophy") deliberately recorded as a caveat, not an event, because the building
it concerns cannot be identified. The fuller design narrative lives in the
maintainers' design notes (kept outside the repository).

In production this dataset would come from a licensed feed rather than a published report.

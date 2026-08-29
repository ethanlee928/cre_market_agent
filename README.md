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
- **No database server.** The quarter loads from `data/seed_2026Q2.json` at
  startup: 70 source records, which normalise to 53 Facts and 17 events. Read-only,
  one quarter, in memory.
- **No observability stack** — no Langfuse, no tracing backend. Every tool call is
  expanded in the UI directly above the answer it produced, which at this scale
  serves the same purpose: you can see which figures were looked up, and which the
  agent declined to invent.
- **One data provider.** Savills, Central London Office Market Watch Q2 2026,
  published 2026-08-06. A second provider is a mapping problem rather than a
  loading problem — definitions and submarket boundaries differ — which is what
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

On load, a briefing with six ranked signals:

|                | Signal                                                                                             | Your exposure                                        |
| -------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 🔴 RISK        | Meridian Quay Tower valued 10% above its 6-peer street (£295 against £269 per m²)                  | Meridian Quay Tower                                  |
| 🔴 RISK        | City Grade B rents falling while Grade A rises (−2.6% against +7.0%)                               | Clerkenwell Works                                    |
| 🔴 RISK        | West End Grade B rents falling while Grade A rises (−11.3% against +4.0%)                          | Mayfair House                                        |
| 🟠 WATCH       | Record 7,700,000 sq ft of completions in 2026, only 35% pre-let                                    | Mayfair House, Clerkenwell Works, Meridian Quay Tower |
| 🟢 OPPORTUNITY | 41 large requirements chasing 21 available options (2.0:1)                                         | 120 Fenchurch Street, Meridian Quay Tower            |
| 🟢 OPPORTUNITY | Insurance & Financial take-up at a series low, yet 41% of space under offer against 20% of take-up | —                                                    |

The sidebar shows an illustrative four-asset portfolio, each tagged with the worst
signal touching it. Signals that hit your holdings sort to the top and expand by default.

### Building vs building — the peer card

The lead signal is a peer comparison: the (fictional) Canary Wharf holding
against seven real, named buildings of similar size and age on the estate —
15 Westferry Circus, 5 Churchill Place, 25 Cabot Square, 20 Bank Street,
20 Churchill Place, 5 Canada Square, 15 Canada Square — each with its floor
area, completion year, whole-building EPC, and a valuation computed from the
VOA 2026 rating list (aggregate rateable value over aggregate office floor
area, £/m², on the 2024 antecedent valuation date). The verdict is computed in
Python, like for like: the asset's valuation against the peer street's median,
never a passing rent against a valuation — the two sit on different bases by
construction. Cells no source publishes render "not published" (15 Canada
Square's does, deliberately left in the peer set), and a district with no
roster — ask about Mayfair — refuses with the reason instead of assembling
peers from memory.

### The join, and the rung it is missing

Each matched building gets its own sentence, computed in Python and closing on a
decision verb — `regear`, `refurbish`, `re-price`, `hold`, `defer capex`,
`start the conversation`. Never "monitor": that is the word that lets a paragraph end
without deciding anything.

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
it costs rather than substituting a number. The seed holds two West End lettings with
achieved rents, £182.50 and £201.00 psf, both of which a naive submarket filter would
hang off a Grade B secondary building. Without a published Grade B level there is no way
to say whether either is a ceiling or a like-for-like, so neither is shown.

The two figures per building are deliberately two and not three: passing-minus-Grade-B
and Grade-A-minus-passing sum exactly to the grade gap, so printing all three invites a
reader to add two of them and count the same spread twice.

The holdings, their passing rents and their EPC ratings are **fictional** — that is Tier 3
data, a client's own rent roll. The market figures they are measured against are not.

Then ask it something:

> **Should we be worried about the City Fringe?**
>
> Yes, particularly for secondary assets. City Fringe vacancy stands at 8.0%, which is
> 150 bps above its long-run average _(Savills, Q2 2026, as of 2026-08-06)_. This directly
> impacts your asset, Clerkenwell Works (38,000 sq ft, Grade B, lease expiry March 2028)...

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
| "…in the City Core or West End specifically"                                    | ◐ Anthropic's 158,138 sq ft now surfaces: `find_market_activity` takes a `submarket`, and matching walks the hierarchy, so a West End question reaches a deal filed under North of Oxford Street East. 13 of 17 events carry no submarket in the source, so the answer states that the count is a floor |
| "Flag Bank Rate, gilt yields, rate shifts"                                      | ⛔ No macro data, no `macro_context` tool. Should refuse; today it may answer freehand from Search grounding without labelling the figure as live                                                                                                                                                       |
| "…this week"                                                                    | ⛔ Source is quarterly, published 2026-08-06. Not answerable from the store at any granularity                                                                                                                                                                                                          |
| "New planning applications over 100,000 sq ft, with developers and ESG targets" | ⛔ No planning-stage data. `development_starts` is construction stage, downstream of planning                                                                                                                                                                                                           |

### 2. Pre-underwriting — micro-market divergence

_Compare submarket benchmarks without reading a 50-page PDF._

| Test query                                                         | Today                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "City Core versus West End Core vacancy"                           | ✅ 5.9% and 4.4%, both against their 10-year averages                                                                                                                                                                                                                                |
| "Compare Canary Wharf, City Core and Mayfair/St James's"           | ◐ `Mayfair` now resolves — 4.4%, and the answer names the node it came from. Canary Wharf still holds **zero facts**, but it resolves as a real submarket, so the reply is the Central London figure explicitly labelled as broader geography rather than a bare "I don't have that" |
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

## Coverage of the brief's eight key areas

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
|     no Canary Wharf, Midtown or Southbank facts in this source; a query for one answers at the        |
|     nearest published parent and says so                                                              |
| 6  | Macro (rates, gilts, Bank Rate)  | none in source     | not surfaced               | gap         |
|     not in a quarterly agency report; no tool, and the model is instructed to refuse rather than      |
|     answer from memory                                                                                |
| 7  | Occupier drivers                 | 4 of 4 metrics     | quality_spread             | partial     |
|     flight-to-quality and ESG are covered; no hybrid-working data is published in this source         |
| 8  | News / named events              | 17 events          | chat only                  | on request  |
|     17 seed transactions plus live Search grounding; no detector, and 13 of the 17 carry no           |
|     submarket                                                                                         |
+----+----------------------------------+--------------------+----------------------------+-------------+
  4 covered · 1 partial · 2 on request · 1 gap
  Computed from 53 facts, 17 events and 4 detectors at render time.
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

119 tests across six files. None needs an API key, a network or a test runner — they
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

The deterministic half is proven by tests; the model's half can only be measured. 30
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

Your portfolio lives in `config/watchlist.yaml`. Edit it, or delete every asset:
the market-wide view works completely with an empty watchlist.

## Data and attribution

Market figures come from **Savills, Central London Office Market Watch Q2 2026**,
published 6 August 2026, reproduced here for a non-commercial proof of concept;
from the **VOA 2026 non-domestic rating list** (open data, aggregated to
building level for Canary Wharf — the method note travels inside
`data/seed_voa_cw_2026list.json`); and from the **non-domestic EPC register**
and named public building records for the Canary Wharf roster, cited row by
row in `data/seed_cw_buildings_2026.json`. Attribution appears in the app
beside every figure.

The four portfolio assets in `config/watchlist.yaml` are **fictional**, and labelled as
such in the sidebar. They exist so the relevance matching has something to match against.
Every market number is real; no holdings are — including Meridian Quay Tower's
rateable value, the fictional half of the peer comparison whose other half is
real VOA data.

Live web results come from Google Search grounding and are labelled separately from
stored figures, because they carry different confidence.

**Provenance, stated plainly:** savills.co.uk returns HTTP 403 to ordinary HTTP
clients, so the article was read in a browser and the figures transcribed by hand
into `data/seed_2026Q2.json`. There is no scraper to inspect. On 29 August 2026 all
70 source records — 47 facts, 6 sector rows, 17 named transactions — were re-read
against the source; no discrepancies were found. Those 70 load as 53 Facts and 17
events (the 6 sector rows normalise into ordinary Facts rather than a parallel
model); the sidebar's larger count adds the Canary Wharf files below. One field is
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
changed in the design, is `docs/designs/canary-wharf-peer-comps.md`.

In production this dataset would come from a licensed feed rather than a published report.

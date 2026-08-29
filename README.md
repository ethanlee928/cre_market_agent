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

---

## Running without an API key

The app still works, in reduced form. The briefing, the five signals, the portfolio
matching — including the per-building reversion arithmetic and its decision verb —
are computed in plain Python from the local dataset, so
they render with no key and no network. Only the chat box is disabled, and the sidebar
says so.

That split is deliberate: the numbers on screen never depend on a model being
available, which is also why they are identical on every run.

---

## What you should see

On load, a briefing with five ranked signals:

|                | Signal                                                                    | Your exposure |
| -------------- | ------------------------------------------------------------------------- | ------------- |
| 🔴 RISK        | City Grade B rents falling while Grade A rises (−2.6% against +7.0%)      | Clerkenwell Works |
| 🔴 RISK        | West End Grade B rents falling while Grade A rises (−11.3% against +4.0%) | Mayfair House |
| 🟠 WATCH       | Record 7,700,000 sq ft of completions in 2026, only 35% pre-let           | Mayfair House, Clerkenwell Works |
| 🟢 OPPORTUNITY | 41 large requirements chasing 21 available options (2.0:1)                | 120 Fenchurch Street |
| 🟢 OPPORTUNITY | Insurance & Financial take-up at a series low, yet 41% of space under offer against 20% of take-up | — |

The sidebar shows an illustrative three-asset portfolio, each tagged with the worst
signal touching it. Signals that hit your holdings sort to the top and expand by default.

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

| Test query                                                                      | Today                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Which occupiers signed over 30,000 sq ft?"                                     | ✅ Nine lettings, largest first, each cited                                                                                                                                                                             |
| "…in the City Core or West End specifically"                                    | ◐ Anthropic's 158,138 sq ft now surfaces: `find_market_activity` takes a `submarket`, and matching walks the hierarchy, so a West End question reaches a deal filed under North of Oxford Street East. 13 of 17 events carry no submarket in the source, so the answer states that the count is a floor |
| "Flag Bank Rate, gilt yields, rate shifts"                                      | ⛔ No macro data, no `macro_context` tool. Should refuse; today it may answer freehand from Search grounding without labelling the figure as live                                                                       |
| "…this week"                                                                    | ⛔ Source is quarterly, published 2026-08-06. Not answerable from the store at any granularity                                                                                                                          |
| "New planning applications over 100,000 sq ft, with developers and ESG targets" | ⛔ No planning-stage data. `development_starts` is construction stage, downstream of planning                                                                                                                           |

### 2. Pre-underwriting — micro-market divergence

_Compare submarket benchmarks without reading a 50-page PDF._

| Test query                                                         | Today                                                                                                                                                                                                  |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "City Core versus West End Core vacancy"                           | ✅ 5.9% and 4.4%, both against their 10-year averages                                                                                                                                                  |
| "Compare Canary Wharf, City Core and Mayfair/St James's"           | ◐ `Mayfair` now resolves — 4.4%, and the answer names the node it came from. Canary Wharf still holds **zero facts**, but it resolves as a real submarket, so the reply is the Central London figure explicitly labelled as broader geography rather than a bare "I don't have that" |
| "Pre-let pipeline ratios by submarket"                             | ◐ `prelet_pct` exists only at Central London level — 35% forecast, 25% under construction, 18% to 2029. No submarket breakdown published                                                               |
| "Southbank comps, rent-free months, headline versus net effective" | ⛔ Southbank holds zero facts. Rent-free incentives and net effective rent are not metrics in this source, and net effective is not derivable without them                                             |

### 3. Tenant risk and sector demand — occupier signals

_Target leasing campaigns, read corporate footprint changes._

The richest data in the store, and currently the least reachable.

| Test query                                              | Today                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Which sectors are expanding?"                          | ✅ Financial \& Banking 5.8m sq ft (+30% YTD), Professional Services 3.58m (+23%), Tech \& Media 3.1m (+26%) — all records, each cited. `get_metric` takes a `sector`, and `list_available` advertises only names it can actually fetch                                                                                     |
| "Are occupiers growing or shrinking?"                   | ◐ `expanding_occupier_share` is reachable — 43% expanding against 12% contracting, 15% new entrants — but no detector surfaces it on the brief                                                                                                                                                                                      |
| "Which sector should we target for a leasing campaign?" | ✅ `sector_demand` computes it and it leads the answer: Insurance \& Financial take-up at 878,112 sq ft, −21% on the five-year average and the weakest in the series, while the sector holds **41% of space under offer against 20% of take-up** — twice its completed weight. Trailing and leading disagree; the leading number is the one that has not happened yet |
| "Will our tenant renew?"                                | ⛔ No tenant-level data, and prediction is out of scope for a sourced system. The honest answer is the market base rate: 1.4m sq ft of the 3.1m sq ft under offer is regear, 93% of it from occupiers already in 50,000 sq ft or more                                                                                               |

Goal 3 closed with two of the three things planned, and deliberately without the
third. `get_metric` took a `sector` parameter, and `sector_demand` now fires on
trailing take-up disagreeing with the leading pipeline.

The sector vocabulary was dropped on purpose. Joining `Insurance & Financial` to
`Financial & Banking` would have asserted that two Savills groupings are the same
sector, and insurance is not banking — the source uses different cuts in different
tables and never claims they are equivalent. The stronger signal needs no such
inference: it compares one sector against *itself*, take-up versus space under
offer, which is a comparison the source fully supports and never draws.

One figure had to move to make it computable. "41% of space currently under offer"
lived inside a note string, and a detector parsing prose for a number is the exact
defect this store exists to prevent. It is now `share_of_under_offer_pct` in the
fact's `extras`, declared in `EXTRA_FIELDS`, with the original sentence kept for
provenance. Same defect class as the sector rows that were unreachable before.

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

69 tests across four files. None needs an API key, a network or a test runner — they
are plain scripts, so there is nothing to install. Most guard failures that only appear
once a second quarter is merged: periods sorting by their string name, a metric changing
grain between reports, a detector subtracting two figures from different years. The
newest file guards the join — that a lease window opening in July 2026 rejects a March
2026 break, and that a `Fact` carrying a `None` level never has a comparison built
against it.

---

## Troubleshooting

| Symptom                                   | Cause                                      | Fix                                                                                    |
| ----------------------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `command not found: uv`                   | `uv` not installed                         | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then reopen the terminal           |
| Sidebar says "No `GOOGLE_API_KEY`"        | `.env` missing or key blank                | `cp .env.example .env` and paste your key                                              |
| "Your Google API key was rejected"        | Bad or revoked key                         | Generate a new one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| "Gemini rate limit reached"               | Free-tier quota                            | Wait a minute. The briefing keeps working, it needs no API                             |
| "That model is not available on your key" | `CRE_MODEL` unavailable                    | Run `probe_models.py`, then set `CRE_MODEL` in `.env`                                  |
| Blank page on first load                  | Browser opened before the server was ready | Reload the page                                                                        |
| Port 8501 already in use                  | Another Streamlit running                  | `uv run streamlit run app.py --server.port 8502`                                       |

---

## Configuration

All in `.env`:

| Variable         | Default            | Purpose                                                |
| ---------------- | ------------------ | ------------------------------------------------------ |
| `GOOGLE_API_KEY` | _(none)_           | Required for chat. The briefing works without it       |
| `CRE_MODEL`      | `gemini-3.7-flash` | Swap models without touching code                      |
| `CRE_THINKING`   | `medium`           | `low`, `medium` or `high`. Lower is faster and cheaper |

Your portfolio lives in `config/watchlist.yaml`. Edit it, or delete every asset:
the market-wide view works completely with an empty watchlist.

---

## Project structure

```
├── app.py                      Streamlit chat surface; briefing is message one
├── src/cre_agent/
│   ├── store.py                the spine. Only module that reads raw JSON
│   ├── signals.py              4 deterministic detectors + the relevance join
│   ├── watchlist.py            your assets, lease windows, per-signal matching
│   ├── submarkets.py           the controlled vocabulary and its hierarchy
│   └── llm/gemini.py           the agent loop, streams tool calls to the UI
├── config/
│   ├── watchlist.yaml          your assets (three fictional ones ship by default)
│   └── submarkets.yaml         controlled vocabulary so "Mayfair" resolves to "West End"
├── data/
│   └── seed_2026Q2.json        53 facts, 17 events, all Savills-sourced
├── scripts/
│   ├── probe_models.py         which models does this key see?
│   └── smoke_test.py           four API checks
└── tests/
    ├── test_time_axis.py       19  store, periods, detectors
    ├── test_submarket_resolution.py  18  aliases resolve up, events match down
    ├── test_sector_demand.py   12  the trailing-vs-leading detector
    └── test_watchlist_join.py  20  lease windows, reversion, the E-4 trap
```

**The design rule:** `store.py` is the only module that touches raw data. Detectors and
agent tools both query through it. Metrics and signal detection are deterministic Python;
interpretation, news and conversation are the model's job. That line is drawn on purpose.

---

## Data and attribution

Market figures come from **Savills, Central London Office Market Watch Q2 2026**,
published 6 August 2026, reproduced here for a non-commercial proof of concept.
Attribution appears in the app beside every figure.

The three portfolio assets in `config/watchlist.yaml` are **fictional**, and labelled as
such in the sidebar. They exist so the relevance matching has something to match against.
Every market number is real; no holdings are.

Live web results come from Google Search grounding and are labelled separately from
stored figures, because they carry different confidence.

**Provenance, stated plainly:** savills.co.uk returns HTTP 403 to ordinary HTTP
clients, so the article was read in a browser and the figures transcribed by hand
into `data/seed_2026Q2.json`. There is no scraper to inspect. On 29 August 2026 all
70 source records — 47 facts, 6 sector rows, 17 named transactions — were re-read
against the source; no discrepancies were found. Those 70 load as 53 Facts and 17
events, which is the count the sidebar reports: the 6 sector rows normalise into
ordinary Facts rather than a parallel model. One field is an inference rather than a
quote: Anthropic's letting at 1 Triton Square is tagged to the North of Oxford
Street East submarket, which is correct but is not stated in the article.

In production this dataset would come from a licensed feed rather than a published report.

---

## Not built

Stated plainly rather than discovered:

- No automated data ingestion. The quarterly dataset was harvested once, by hand,
  by reading the published article and transcribing the figures. All 70 records
  were later re-checked against the source; see **Data and attribution**.
- One quarter of history, so trends are quoted from the source rather than computed.
  Q1 2026 is the obvious next harvest and would make quarter-on-quarter computed.
- Tests cover the store, the time axis and the detectors. Nothing covers the
  Streamlit layer or the agent loop; both are exercised by hand.
- No authentication, no multi-user, no deployment pipeline. Local run only.

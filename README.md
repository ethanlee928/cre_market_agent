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

The app still works, in reduced form. The briefing, the four signals, the portfolio
matching and all the figures are computed in plain Python from the local dataset, so
they render with no key and no network. Only the chat box is disabled, and the sidebar
says so.

That split is deliberate: the numbers on screen never depend on a model being
available, which is also why they are identical on every run.

---

## What you should see

On load, a briefing with four ranked signals:

| | Signal |
|---|---|
| 🔴 RISK | City Grade B rents falling while Grade A rises (−2.6% against +7.0%) |
| 🔴 RISK | West End Grade B rents falling while Grade A rises (−11.3% against +4.0%) |
| 🟠 WATCH | Record 7,700,000 sq ft of completions in 2026, only 35% pre-let |
| 🟢 OPPORTUNITY | 41 large requirements chasing 21 available options (2.0:1) |

The sidebar shows an illustrative three-asset portfolio, each tagged with the worst
signal touching it. Signals that hit your holdings sort to the top and expand by default.

Then ask it something:

> **Should we be worried about the City Fringe?**
>
> Yes, particularly for secondary assets. City Fringe vacancy stands at 8.0%, which is
> 150 bps above its long-run average *(Savills, Q2 2026, as of 2026-08-06)*. This directly
> impacts your asset, Clerkenwell Works (38,000 sq ft, Grade B, lease expiry March 2028)...

Or ask who is actually active:

> **Who's taking space right now?**
>
> Anthropic took 158,138 sq ft at 1 Triton Square, NW1, OpenAI acquired 77,521 sq ft
> at Jahn Court, 34 York Way, N1, and Fractile AI signed for 21,734 sq ft at the
> JJ Mack Building, EC1 at £115.00 psf *(Savills, Q2 2026)*...

Expand the tool-call panel above any answer to see exactly which figures the agent
looked up. Every number it states comes from a tool call, never from memory.

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
uv run python tests/test_time_axis.py
```

19 tests over the store and detectors. Needs no API key, no network and no test
runner — it is a plain script, so there is nothing to install. Most of these
guard failures that only appear once a second quarter is merged: periods sorting
by their string name, a metric changing grain between reports, a detector
subtracting two figures from different years.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `command not found: uv` | `uv` not installed | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then reopen the terminal |
| Sidebar says "No `GOOGLE_API_KEY`" | `.env` missing or key blank | `cp .env.example .env` and paste your key |
| "Your Google API key was rejected" | Bad or revoked key | Generate a new one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| "Gemini rate limit reached" | Free-tier quota | Wait a minute. The briefing keeps working, it needs no API |
| "That model is not available on your key" | `CRE_MODEL` unavailable | Run `probe_models.py`, then set `CRE_MODEL` in `.env` |
| Blank page on first load | Browser opened before the server was ready | Reload the page |
| Port 8501 already in use | Another Streamlit running | `uv run streamlit run app.py --server.port 8502` |

---

## Configuration

All in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | *(none)* | Required for chat. The briefing works without it |
| `CRE_MODEL` | `gemini-3.7-flash` | Swap models without touching code |
| `CRE_THINKING` | `medium` | `low`, `medium` or `high`. Lower is faster and cheaper |

Your portfolio lives in `config/watchlist.yaml`. Edit it, or delete every asset:
the market-wide view works completely with an empty watchlist.

---

## Project structure

```
├── app.py                      Streamlit chat surface; briefing is message one
├── src/cre_agent/
│   ├── store.py                the spine. Only module that reads raw JSON
│   ├── signals.py              3 deterministic detectors
│   ├── watchlist.py            portfolio matching + submarket hierarchy
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
    └── test_time_axis.py       19 tests, no pytest needed
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
70 records — 47 facts, 6 sector rows, 17 named transactions — were re-read against
the source; no discrepancies were found. One field is an inference rather than a
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

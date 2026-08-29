# London Office Market Monitor

An AI agent that monitors the Central London office market for a commercial real
estate team. Opens with a ranked brief filtered to the buildings the user holds,
then answers follow-ups with sourced figures.

## Architecture: deterministic spine, agentic edges

This is the governing decision (design doc, Approach C). Hold the line on it.

**Plain Python — must be identical on every run, and must work with no API key:**
- `src/cre_agent/store.py` — fact store, period parsing, delta normalisation
- `src/cre_agent/signals.py` — detectors + severity ranking
- `src/cre_agent/watchlist.py` — asset loading, submarket hierarchy, relevance matching
- Every figure rendered on the brief page

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
  are not. That labelling is the honesty guarantee.
- **`yaml.safe_load` only.** `config/` is user-editable; `yaml.load` is RCE.
- **Units travel with numbers.** Use `Delta.render()`. Never f-string a raw bps
  value — it will print as a percentage and be wrong by 100x.
- **Levels may be unpublished.** `Fact.value` can be `None` with deltas present
  (e.g. West End vacancy, West End `grade_b_rent_avg`). Read the delta; never
  assume a level. `quality_spread` is the reference implementation.
- **Do not remove `include_server_side_tool_invocations=True`** from `tool_config`
  in `llm/gemini.py`. Mixing our function declarations with Google Search
  grounding in one request returns 400 without it.

## Entry point

One surface: `uv run streamlit run app.py`. There is deliberately no CLI — see
the comment in `pyproject.toml`.

## Data

`data/seed_2026Q2.json` — Savills Central London Office Market Watch Q2 2026,
published 2026-08-06. 47 facts, 17 events, 6 sector rows. Real, harvested, cited.
`savills.co.uk` returns 403 to generic HTTP clients, which is why retrieval goes
through Gemini Search grounding rather than `httpx`.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec

# Handoff: The watchlist join — the product's core lever

**Status:** partially built (submarket matching works; lease matching does not)
**Estimated effort:** the majority of remaining time
**Owner:** unassigned
**Prereq:** none, but `cli.py` / `app.py` must exist for any of this to be visible

---

## Why this is the highest-value work in the project

Three layers exist in this market:

- **Data providers** (CoStar, MSCI, EG Radius, and the research desks at
  JLL / CBRE / Savills / Knight Frank) know the market in enormous depth. They
  do not know which buildings you own.
- **General research agents** (Manus, deep-research tools, Perplexity) can
  answer any one-off question. They have no memory, so they cannot tell you what
  *changed*, and they are not reproducible, so their numbers cannot go in a
  board pack.
- **This system** is the only layer that can join a market fact to a specific
  building the user owns.

That join is the entire competitive position. Everything else in the product —
chat, coverage of the eight key areas, the second-source story, macro — is
supporting evidence that the join can be trusted.

The task brief asks the system to help a team *"stay ahead of market shifts, spot
risks/opportunities early, and support informed decision-making."* All three are
the same capability: **detect what changed, rank it, filter it to the assets we
hold.** A static PDF already covers eight key areas beautifully. What it cannot
do is know that new supply arriving in 2026 matters because a lease break falls
in the same window.

---

## The one sentence to build toward

If only one thing works, make it this — rendered on the front page, before the
user types anything:

> **Mayfair House** — your break falls Q3 2027, the same window in which 7.7m sq ft
> of new supply completes at 35% pre-let. West End Grade B rents are down 11.3%
> YoY. Your passing rent of £82 psf sits above the £71 Grade B average.
> *Expect a downward reversion at review — start the regear conversation now.*
> — Savills Central London Office Market Watch Q2 2026, published 6 Aug 2026

Read it as a spec. It requires, in order:

1. A named asset, from the watchlist — **already works**
2. A **break date**, and the ability to compare it to a market event window — *missing*
3. A supply fact with a completion year — already in the seed
4. A grade-and-submarket rent delta — already in `quality_spread`
5. A **passing rent** compared against a market average — *missing*
6. A recommended action in domain language — *missing from the narrative contract*
7. A citation with an as-of date — already works

Items 2, 5 and 6 are the whole of this document.

---

## Current state

**`src/cre_agent/watchlist.py` (137 lines)**

```python
@dataclass(frozen=True)
class Asset:
    name: str
    submarket: str
    grade: str | None = None
    sqft: int | None = None
    lease_expiry: str | None = None     # present but UNUSED by any detector
    note: str | None = None

class Watchlist:
    def matching(self, submarket=None, grade=None, min_sqft=None) -> list[str]
```

`SubmarketIndex` handles the hierarchy and aliases properly — an asset in
"Mayfair" already matches a signal about the "West End" via
`index.covers(asset.submarket, signal_submarket)`. Do not rebuild this; it works
and it fixed a real defect (review finding H4).

**`config/watchlist.yaml`** holds three clearly-labelled fictional assets:

| Name | Submarket | Grade | Sq ft | Expiry |
|---|---|---|---|---|
| 120 Fenchurch Street | City Core | A | 145,000 | 2031-06 |
| Mayfair House | Mayfair | B | 24,000 | 2027-09 |
| Clerkenwell Works | City Fringe | B | 38,000 | 2028-03 |

The file header states plainly that the holdings are fictional while every market
figure is real and sourced. **Preserve that labelling** — it is the honesty
guarantee that makes the demo defensible.

**`src/cre_agent/signals.py`** — three detectors (`quality_spread`,
`supply_shock`, `large_occupier_squeeze`), each calling
`_match(watchlist, submarket=..., grade=...)`. Matching is **submarket and grade
only**. No detector reads `lease_expiry`, so today the join says "you hold two of
these" and stops there.

---

## Work item 1 — extend the `Asset` model

Add to `Asset` in `src/cre_agent/watchlist.py`, all optional:

```python
break_date: str | None = None        # "YYYY-MM", the next tenant/landlord break
passing_rent_psf: float | None = None  # £ per sq ft per annum, what is being paid now
epc_rating: str | None = None        # "A".."G"
tenant: str | None = None            # covenant, drives risk framing
```

Domain notes for whoever implements this:

- **Passing rent** is the rent currently being paid. It is not the market rent.
  The gap between the two is the **reversion**, and its direction is the whole
  point: passing *above* market means income falls at the next review or
  re-letting; passing *below* market means upside. This single comparison turns a
  market statistic into a P&L consequence.
- **Break date matters more than lease expiry.** A break is the next moment the
  income can actually disappear. Every detector that reasons about timing should
  prefer `break_date` and fall back to `lease_expiry`.
- **EPC** is the energy rating. Under the UK MEES regime, sub-standard ratings
  restrict a landlord's legal ability to let the space. Tighter future thresholds
  have been consulted on but the dates are unsettled — **do not hard-code a
  compliance deadline into the code or the narrative.** Flag exposure ("EPC D,
  below the standard several proposals would require") and let the agent
  attribute any specific date to a live, cited source.

Populate the three fictional assets in `config/watchlist.yaml` with plausible
values. Mayfair House must carry `break_date: "2027-09"` and a `passing_rent_psf`
above the West End Grade B average, because that is what makes the target
sentence fire.

Keep every new field optional. **The empty-watchlist path must stay fully
functional** — that is premise P2 in the design doc, and a stated success
criterion: with no assets configured, the market-wide view is still complete.

---

## Work item 2 — lease-window matching

Extend `Watchlist.matching()` with a time dimension:

```python
def matching(self, submarket=None, grade=None, min_sqft=None,
             lease_event_between: tuple[str, str] | None = None,
             passing_rent_above: float | None = None,
             epc_worse_than: str | None = None) -> list[str]
```

`lease_event_between` takes `("2026-01", "2028-12")` and matches an asset whose
`break_date` — or `lease_expiry` if no break — falls inside the window.

Comparison should be on `(year, month)` tuples parsed from the `YYYY-MM` strings.
Do not add a date-parsing dependency for this; the seed periods are already
handled by `Period` in `store.py` and this is a simpler case.

Consider returning richer objects than bare names. `Signal.affected` is currently
`list[str]`, which is enough to render "you hold 2 of these" but not enough to
render *why* each asset matched. If the narrative is to say "your break falls in
the same window", the match reason has to survive the trip. Changing `affected`
to a small `Match(asset_name, reason)` dataclass is the cleaner path — check the
call sites in `signals.py` and any renderer before committing to it.

---

## Work item 3 — teach the detectors to use it

**`supply_shock`** — currently reports 7.7m sq ft of 2026 completions at 35%
pre-let, market-wide. It should additionally match assets whose break or expiry
falls within roughly ±12 months of the completion wave, in the same submarket
family. That is the timing collision that produces the target sentence.

**`quality_spread`** — currently matches on submarket and grade. It should also
compare each matched asset's `passing_rent_psf` against the relevant
`grade_b_rent_avg` or `grade_a_rent_avg` and state the direction of the
reversion.

**`submarket_divergence`** (being added under
`docs/handoffs/coverage-floor.md`) — match on submarket; if an asset in an
elevated-vacancy submarket also has a near-term break, escalate severity.

**A new consideration for ranking.** `Signal.sort_key()` currently orders by
"has affected assets, then severity". Once lease timing exists, the better
ordering is severity weighted by **irreversibility**: a rent move you can wait
out ranks below a supply wave landing on a break date you cannot move. This is a
deliberate design decision, not a refactor — discuss before changing, and if it
changes, keep it to a single ordering rule. Three competing sort orders was a
prior review finding.

---

## Work item 4 — the narrative contract

Every signal's narrative, when it touches a watchlist asset, must end in a
**decision**, not an observation. "Monitor the situation" is a failure.

Acceptable closing verbs, in the language the audience actually uses:

- **regear** — renegotiate the existing lease rather than let the tenant leave
- **refurbish** — spend capex to move the asset up a grade
- **re-price** / **market now** — bring space to market ahead of the supply wave
- **hold** — do nothing, deliberately, with a stated reason
- **defer capex** — wait for a better window
- **start the conversation** — engage the tenant before the break notice date

Wire this into `SYSTEM_PROMPT` in `src/cre_agent/llm/gemini.py`, which already
enforces the sourcing rules. Add roughly:

> When a signal touches an asset on the user's watchlist, close with what they
> should consider doing about that specific building, using the language a CRE
> professional would use: regear, refurbish, re-price, hold, defer capex. Never
> close with "monitor". Never state a rent without saying whether it is headline
> or net effective, and never state a figure without its submarket, grade, period
> and source.

**Guardrails that must not be relaxed:**

- The agent narrates; it never computes. Every figure comes from a tool call.
  Reproducibility is a differentiator in the pitch and it dies the moment a
  number is generated rather than looked up.
- Headline rent and net effective rent are different things. The seed carries
  headline only, and it publishes no incentives data. If asked about net
  effective, the correct answer is that the source does not state it.
- Fictional holdings must be labelled as fictional wherever they render.

---

## Acceptance criteria

1. With the shipped `config/watchlist.yaml`, the front page renders the target
   sentence — or something materially equivalent — for Mayfair House, without
   the user typing anything.
2. At least two detectors match on a lease event, not merely on submarket.
3. Every watchlist-matched signal closes with a decision verb.
4. Deleting every asset from `config/watchlist.yaml` leaves the market-wide brief
   complete and useful. This is a hard requirement.
5. Two consecutive runs produce identical signals, headlines and figures. Only
   the narrative prose may vary.
6. A unit test covers lease-window matching, including the boundary cases: no
   break date, break date outside the window, and an empty watchlist.

---

## Open questions worth deciding early

- Should `Signal.affected` become a structured match object? Needed for "why did
  this asset match", but it touches every detector and the renderer.
- Should the agent be able to *add* an asset to the watchlist from chat? The
  design doc mentions offering to add a submarket. It is a nice demo beat and a
  write path into user config — decide whether that write is in scope for a PoC.
- Where does passing rent come from for a real user? Fine to hard-code in YAML
  for the PoC, but it is worth one slide: this is Tier 3 data — the client's own
  rent roll — and it is the data no competitor can obtain.

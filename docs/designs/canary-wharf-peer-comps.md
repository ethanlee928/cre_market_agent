# Design: Canary Wharf peer comps — building vs building as the leading demo

Written 2026-08-29
Branch: main
Repo: cre_market_agent (local, no remote)
Status: IMPLEMENTED 2026-08-29 — see **AS BUILT** at the end for where the
harvest corrected the design (like-for-like verdicts, area-weighted
aggregation, one tool instead of two)
Sibling: `docs/designs/watchlist-join-relevance-lens.md` (CLOSED — this design extends
its class-2 evidence, the named comparable, from "a letting that happened to be
reported" into a deliberately assembled peer set)

## Problem Statement

Industry feedback (2026-08-29, practitioner review of the prototype): the
higher-impact decision surface is **building-to-building comparison** — a held
building against peers of similar size and age in the same district, on vacancy,
rent, and efficiency, answering "is the group aligned with the market, more
expensive, or cheaper." The same reviewer's objection to AI tools generally:
*"not enough detailed comparison — the data is vague, could not give indication,
unless you dive into details. Drill down → blueprint → calculation."*

Today the system answers that question at the wrong granularity. `_reversion` in
`signals.py` compares a holding's passing rent to a **submarket grade average** —
the right arithmetic against the wrong comparator. The reviewer wants the
comparator to be *named buildings*. And the coverage table already confesses the
geographic half of the gap: area 5 carries the caveat "no Canary Wharf, Midtown
or Southbank facts in this source."

The objection about AI trust is not a threat to this design — it is this
codebase's founding decision. The deterministic spine computes; the model
narrates. What is missing is data at building granularity, not architecture.

## Why Canary Wharf

The pilot district is chosen for data density, not client relevance:

- **A closed set.** ~35–40 named buildings on one estate under one dominant
  landlord. A complete roster is a one-time hand harvest, not an open crawl.
- **Building-level vacancy is newsworthy there.** HSBC vacates 8 Canada Square
  at lease expiry (early 2027; 1.1m sq ft reworking has planning lodged); Citi's
  25 Canada Square is mid-refurbishment. For most of London, building-level
  vacancy exists only inside CoStar; at Canary Wharf the majors are individually
  reported in the press and agent research.
- **Open data covers the rest.** The VOA 2026 rating list (compiled 2026-04-01,
  valued as at 2024-04-01) is bulk-downloadable and carries a summary valuation
  in £/m² for every hereditament on the estate. The non-domestic EPC register is
  bulk-downloadable quarterly with floor area, rating, and lodgement date.
- **A submarket aggregate exists to anchor on.** Colliers' London Offices
  Snapshot publishes Canary Wharf vacancy and prime rents quarterly (Q1 2026:
  vacancy 8.3%, down from 13.8% a year earlier; prime £57.50 psf) — harvestable
  in exactly the pattern of the existing Savills seed.

Verification (2026-08-29): VOA rating list downloads at
`voaratinglists.blob.core.windows.net/html/rlidata.htm`; EPC bulk downloads at
`epc.opendatacommunities.org` (non-domestic API/CSV); Colliers snapshot at
colliers.com (London Offices Snapshot, Jan 2026); 8 Canada Square reworking via
CoStar news; Citi refurbishment widely reported.

## What the pilot proves

One demo interaction, in front of the reviewer:

> "Is ⟨our Canary Wharf holding⟩ value for money against its neighbours?"

and the answer is a table of named peers — each with size, age, EPC, a valuation
figure, an achieved rent where one was reported — every cell sourced and dated,
a verdict sentence computed in Python closing on `ACTIONS`, and an explicit
refusal for any cell no source publishes. The refusal *is* the feature: it is
the behaviour the reviewer says AI does not have.

## Constraints (inherited, all binding)

- Every number carries source and as-of date (P3). "Not published" renders as
  "not published".
- Every figure on the brief is plain Python. The model narrates; it never
  computes.
- Fictional holdings labelled fictional wherever they render — and this design
  raises the stakes: a fictional passing rent will sit in a table beside real
  named buildings' real figures, which *increases* its apparent authority.
- A comparison may only claim a peer it can name a reason for — the
  `match_reasons` invariant, applied to comps.
- Empty watchlist stays fully functional; the peer detector simply does not fire.
- `yaml.safe_load` only; units travel with numbers.

## Approaches Considered

**A: comps as a parallel store.** A `comps.py` that loads its own JSON and never
touches `store.py`. Rejected: `store.py`'s docstring is "the only module that
touches raw seed JSON", and that choke point is why data defects get fixed once.
A second loader is where the E-1 disease grows back.

**B: building measurements as Facts with a `building` identity axis — chosen.**
`Fact` already treats `sector` as part of identity (E-3). `building` is the same
move: rateable values, achieved rents, floor areas become ordinary Facts that
inherit Source/Period/Delta/E-4-null machinery for free. Per-source seed files
give per-fact provenance with zero new code, because `Store.load` already stamps
a `Source` per file. The building *roster* (identity + physical constants) is
the only new structure.

**C: full asset-first drill-down page.** The reviewer's "geography → blueprint →
calculation" flow as a per-asset Streamlit view. Deferred, not rejected: the
brief-card-plus-chat form proves the capability; a dedicated page is a rendering
decision to take after the demo lands. Noted in Open Questions.

## Recommended Approach

### 1. Vocabulary (config/submarkets.yaml)

Add `canary_wharf` as a child of `docklands`, and move the alias "Canary Wharf"
from `docklands` onto it ("E14" stays on `docklands` — E14 is wider than the
estate). Colliers publishes figures for "Canary Wharf", not all of Docklands;
filing them one node too high is the H4 disease in reverse. Facts filed under
"Canary Wharf" resolve to the new node; `climb=True` still reaches Docklands and
Central London ancestors; events and assets in Canary Wharf are reached by
Docklands questions via `covers()`.

### 2. Data — one seed file per source

`Store.load` globs `seed_*.json` and stamps each file's `source` block onto its
facts. Provenance therefore falls out of file layout:

| File | Source | Contents |
|---|---|---|
| `data/seed_colliers_cw_2026Q1.json` | Colliers, London Offices Snapshot (Jan 2026) | Facts: `vacancy_rate` (8.3%, yoy from 13.8%), `prime_rent` (£57.50, yoy +4.55%), submarket "Canary Wharf". Events: HSBC vacating 8 Canada Square (type `vacating`, date, sqft), 25 Canada Square refurbishment (type `refurbishment`). Event `type` is an open vocabulary read from data — no code change. |
| `data/seed_voa_cw_2026list.json` | VOA, 2026 non-domestic rating list | Per-building Facts: `rateable_value_avg` in **`gbp_psm`**, `building` set, period `2024` with note "2026 rating list; antecedent valuation date 1 April 2024". |
| `data/seed_cw_buildings_2026.json` | EPC register primary; per-row provenance in `note` | Top-level `buildings` roster: name, submarket, sqft, year_built, floors, epc_rating, per-row `note` citing where size/age came from. |

**Acquisition mechanics — real data, mostly downloads, not crawling.** The VOA
rating list is an unauthenticated bulk CSV download (nationwide; filter to the
estate's addresses by script, hand-check the matches). The Colliers snapshot is
a published PDF, harvested by hand like the Savills seed. The EPC bulk download
requires a free registered account — for 12–15 buildings the no-login
per-certificate lookup on find-energy-certificate.service.gov.uk suffices for
the pilot. Building specs and vacancy events come from public records and press,
preferring open citations over paywalled ones (CoStar) and 403-prone hosts
(savills.co.uk). Every figure lands in a seed file with its source; the only
fictional record in the demo remains the held asset, labelled as such.

**The VOA aggregation rule, stated up front:** hereditaments are floors and
suites, not buildings — a tower has dozens. The building figure is the **median
£/m² across office hereditaments matched to the building's address**, with the
hereditament count carried in `extras` and the rule named in the fact's `note`.
Summing rateable values would conflate size with price; the median is a price
level. Address matching is the fiddliest part of the harvest and is done once,
by hand, at harvest time — never at runtime.

EPC certificates are also per-unit; same rule (the building's rating is the most
recent whole-building certificate where one exists, else the modal rating, named
in the note).

### 3. Store extension (store.py — contained, ~60 lines)

- `Fact.building: str | None = None`; `"building"` added to `CORE_FIELDS`.
- Both dedupe keys in `load()` extend to include building — mandatory, or two
  buildings' `rateable_value_avg` for the same period collide and one silently
  drops.
- `find(building=...)` filter defaulting to `"__any__"`, mirroring `sector`;
  `get()` defaults `building=None` so a market-level lookup can never silently
  return a building fact — the exact E-3 discipline, third verse.
- `TOP_LEVEL_KEYS` += `"buildings"`; rows parse into a frozen `Building`
  dataclass (name, submarket, sqft, year_built, floors, epc_rating, note,
  source); `Store.buildings` list and `find_buildings(submarket=...)`.
- `Fact.render_value()` gains `gbp_psm` (`£X.XX /m²`). The store renders £/m²
  *as* £/m²; conversion is someone else's explicit act (see the trap below).

### 4. The matcher and the arithmetic (new module: comps.py, ~150 lines)

```python
@dataclass(frozen=True)
class PeerMatch:
    building: Building
    reasons: tuple[str, ...]   # non-empty by construction

MIN_PEERS = 3
PSF_PER_PSM = 1 / 10.7639     # the E-5 sibling: £/m² rendered as psf is ~11x wrong

def peer_set(asset, store) -> list[PeerMatch] | Refusal
def compare(asset, store) -> Comparison | Refusal
```

Deterministic rules, each contributing a reason string:

- same submarket node (via `SubmarketIndex.same_node`, never string compare);
- sqft within ×/÷ 2.0 of the asset;
- `year_built` within ±10 years, **when the asset declares an age** — the
  fictional assets currently don't; a rule with no input skips and says so
  rather than silently passing everything.

Below `MIN_PEERS` qualifying peers, `peer_set` returns a `Refusal` carrying the
count and the failed rules — "2 qualifying peers; 3 required" is a rendered
sentence, not an exception.

`compare` follows `_reversion`'s two-figure discipline:

- **Benchmark 1 — valuation:** median of peers' `rateable_value_avg`, converted
  by `PSF_PER_PSM` in exactly one place, always rendered with "valued as at
  April 2024". A 2024 valuation against a 2026 passing rent is honest *only*
  with the date attached.
- **Benchmark 2 — achieved:** median `rent_psf` across letting events in the
  peer set, only when ≥ 2 events carry a rent; otherwise that row renders "not
  enough reported lettings to benchmark".
- Verdict: passing vs benchmark × sqft = £/yr above or below, verb from
  `signals.ACTIONS`. Never three figures that a reader can double-count.

### 5. The detector (signals.py: `peer_gap`, ~60 lines)

A new entry in `DETECTORS`. For each watchlist asset whose submarket has a
roster: run `compare`, emit one Signal with the comparison table (markdown) in
`detail`, the asset in `affected`, the reason/verdict in
`match_reasons`/`match_actions`, and the VOA facts used in `evidence` so
`citations()` renders them. A `Refusal` emits nothing — a signal that cannot
name its peers does not fire. Because it is a Signal, the existing brief loop in
`app.py` renders it with zero rendering changes, and the existing sort (affected
assets first) makes it lead naturally.

### 6. The demo surface

- **Watchlist:** add a fourth fictional asset in Canary Wharf. The name must not
  collide with a real estate building (e.g. "Meridian Quay Tower" — verify
  against the roster at harvest time). Grade A, mid-size (~180–220k sq ft, so
  the ×/÷2 band catches the mid-rise peers, not One Canada Square), with
  `year_built` (new optional Asset field), and a passing rent set a few pounds
  off the VOA-implied street level so the verdict has content either way.
- **Agent tools (llm/gemini.py):** two declarations — `get_peer_set(asset)` and
  `compare_building(asset)` — dispatching in `_run_tool` to comps.py and
  returning the deterministic result as a dict, refusals included. One
  SYSTEM_PROMPT sentence naming the capability and its district limit.
- **Seeded question (app.py):** add "Is ⟨asset⟩ priced right against its Canary
  Wharf peers?" to `seeds`.
- **Coverage (coverage.py):** new `KeyArea 9, "Peer comparison (building vs
  building)"` with `detector="peer_gap"`, `tools=("compare_building",
  "get_peer_set")`, caveat "Canary Wharf only; VOA valuations as at April 2024;
  building-level vacancy limited to reported majors". Update area 5's caveat —
  Canary Wharf is no longer unpublished; Midtown and Southbank still are.

### 7. Two traps promoted to invariants (CLAUDE.md)

- **£/m² never renders as psf.** VOA publishes per square metre. One conversion
  constant, one call site, one pinned test. Same defect class as the bps trap.
- **A thin peer set refuses; it never averages.** Two buildings are an anecdote.
  `MIN_PEERS` is asserted in code, and a test proves the refusal sentence
  renders instead of a median-of-two.

## Work items, in order

| # | Item | Touches | Size |
|---|---|---|---|
| 1 | Harvest: Colliers facts + events file | data/ | small |
| 2 | Harvest: roster (12–15 buildings) + VOA medians + EPC | data/ | **the long pole — 1–2 days of careful manual matching** |
| 3 | Vocabulary: `canary_wharf` node | config, test_submarket_resolution | small |
| 4 | Store: `Fact.building`, `Building`, dedupe keys, filters, `gbp_psm` | store.py | ~60 lines |
| 5 | comps.py: matcher, refusals, conversion, compare | new | ~150 lines |
| 6 | `peer_gap` detector | signals.py | ~60 lines |
| 7 | Watchlist: fictional CW asset, `year_built` field | config, watchlist.py | small |
| 8 | Agent tools + SYSTEM_PROMPT line + seeded question | gemini.py, app.py | ~50 lines |
| 9 | Coverage area 9 + area 5 caveat | coverage.py | small |
| 10 | `tests/test_peer_comps.py` (~20 tests, below) | tests/ | ~200 lines |
| 11 | Eval cases (~3) + full eval run (SYSTEM_PROMPT and declarations changed) | evals/ | small |
| 12 | README + CLAUDE.md invariants | docs | small |

Steps 1–2 need no code and can start immediately; 3–6 are testable without any
harvest by using a synthetic roster fixture, so the two tracks parallelise.

## Test plan (test_peer_comps.py)

- matcher: each rule admits/rejects correctly; every match carries ≥1 reason;
  reasons name figures, not adjectives.
- refusal below `MIN_PEERS`, and when the asset's submarket has no roster.
- `PSF_PER_PSM` pinned to 4 decimal places; a £400/m² fact compares as ~£37 psf.
- benchmark 2 refuses below 2 rented lettings.
- verdict ∈ `ACTIONS`; "monitor" never appears (extend the existing assertion).
- fictional label travels into the comparison rendering.
- empty watchlist: `peer_gap` emits nothing, brief unchanged.
- store: `get("prime_rent", "Canary Wharf")` never returns a building fact;
  two buildings' same-metric facts both survive dedupe.
- submarket: "Canary Wharf" resolves to the new node; Docklands question reaches
  a Canary Wharf event.

## Success Criteria

1. The demo question returns a named-peer table where every figure traces to a
   tool call, every cell carries source + as-of, and unpublished cells say so.
2. The verdict sentence is byte-identical across runs (it is Python).
3. Asking about a district with no roster ("compare our Mayfair building to its
   neighbours") refuses with the reason, in chat and on the brief alike.
4. The keyless path still renders the full brief including the peer card.
5. Eval run: no regression on the existing 30 cases; the new cases pass.

## Open Questions

- **Per-field provenance on the roster.** File-level Source plus per-row notes
  is the pilot's compromise; production wants a source per field. Revisit when
  a second district forces the roster format to generalise.
- **The asset-first drill-down page** (Approach C): build after the demo lands,
  as a rendering of the same `Comparison` object — geography → peer map →
  table → arithmetic, the reviewer's flow verbatim.
- **Efficiency benchmarks beyond £psf.** Floorplate and NIA:GIA are published
  for the majors; whether they join the table or stay in chat depends on how
  many roster rows can source them. Do not render a column that is mostly
  "not published".
- **Second district.** The design generalises by adding a roster + VOA/EPC
  files; nothing in comps.py is Canary Wharf-specific. Choose the second
  district only after the reviewer reacts to the first.

## AS BUILT (2026-08-29)

Landed the same day, with four corrections the harvest forced on the design.
The tests are `tests/test_peer_comps.py` (27); every count below is asserted
somewhere in the six suites.

**C-3, the correction that matters: comparisons run like for like, never
across bases.** The E14 slice of the VOA file put office valuations at
£197–£341/m² (≈ £18–32 psf) against passing rents near £50 psf — a
systematic basis gap, not a labelling nuance. The planned
passing-rent-versus-valuation-median verdict would have been misleading with
the date attached. As built: the asset carries its own (fictional, labelled)
`rateable_value_psm`, compared against the peer street's valuations — both
£/m², both on the 2024 antecedent valuation date — and its passing rent is
compared only against reported peer letting rents, both contract figures. No
cross-basis difference is ever computed; a test asserts the field does not
exist.

**Aggregation is area-weighted, not an unweighted median.** Serviced-office
operators fragment buildings into hundreds of micro-suites (25-30 Churchill
Place: 459 office hereditaments, most under 20 m²) that would dominate a
per-hereditament median. Each building's figure is aggregate rateable value
over aggregate floor area across its matched office hereditaments; the
hereditament count travels in `extras` so a reader can weigh a 1-hereditament
whole-building assessment (quantum discount embedded) against a 176-suite
multi-let. Across peers, each building then gets one vote in the median.

**One tool, not two.** `compare_building` returns the peer rows, both
benchmarks, the gap, and the verdict in one call, through the same
`comps.compare` + `signals._peer_verdict` path the brief card uses — chat and
page cannot disagree. A separate `get_peer_set` bought nothing but a second
round trip.

**The roster keeps its gaps.** 18 real buildings; 15 carry a VOA figure
(15 Canada Square, 33 Canada Square and 10 Cabot Square had no matchable
office assessment in the E14 slice) and 12 carry a current whole-building
EPC. Those cells render "not published" — 15 Canada Square sits in the demo
peer set precisely because its empty valuation cell shows the refusal
discipline working in front of the reviewer.

Also as built: the `canary_wharf` vocabulary node under `docklands` (the
alias moved down with it); `Fact.building` as a third identity axis with
`get()` defaulting to building-free facts; `Building` + a `buildings`
top-level seed key; underscore-prefixed top-level keys as loader-ignored file
commentary; `rateable_value_avg` in `gbp_psm` rendering as £/m² only;
`PSF_PER_PSM = 0.09290304` as the single conversion site (used to carry the
£/m² gap across the asset's square footage); seeded demo questions for both
the comparison and the off-roster refusal; coverage area 9 wired and firing;
three live eval cases. Actual VOA downloads: 88 MB + 148 MB zipped, 5,870
E14 hereditaments in the working slice, discarded after aggregation.

**Harvest corrections to the aggregate layer.** Verification against the
actual PDFs found the search-era hints wrong twice: the 8.3%/13.8% vacancy
pair is Colliers January 2026 describing **Q4 2025**, and the £57.50 prime
rent is **Carter Jonas** Q1 2026, not Colliers. Both facts are filed at their
true periods under their true publishers, and no open source publishes a
numeric Canary Wharf vacancy for 2026 — that gap is kept as a gap (the
fresher numeral lives only inside CoStar, which is the Phase-3 pitch in one
sentence). Building events (HSBC vacating, Citi refurbishing, PwC under
offer) landed as three new open-vocabulary event types with per-event
citations.

**One live regression, caught and closed.** With the peer card in
`get_signals`, the model began imitating its gap arithmetic on ordinary
market questions (subtracting published Grade A and B levels), flipping
`quality_gap_both_markets` from 3/3 to 0/3. Fixed by extending SYSTEM_PROMPT
rule 1 to forbid model-side arithmetic over tool results explicitly, and by
an `arithmetic_note` in `compare_building`'s payload; the case returned to
3/3 with the peer cases still green.

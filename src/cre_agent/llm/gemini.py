"""The agent loop, Gemini flavour.

A manual function-calling loop rather than the SDK's automatic one, for two
reasons. The UI needs to stream each tool call as it happens, which is the
moment that proves this is an agent and not a chat wrapper. And automatic
calling cannot coexist with a server-side tool in the same request.

Verified in scripts/smoke_test.py: our function declarations and Google Search
grounding CAN share one request, provided tool_config carries
include_server_side_tool_invocations=True. Without it the API returns
400 INVALID_ARGUMENT naming that exact field.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterator

from google import genai
from google.genai import types

from ..signals import detect_all
from ..store import AmbiguousQuery, Store
from ..watchlist import Watchlist

MAX_TURNS = 6


@dataclass
class Event:
    """Streamed to the UI as the loop runs."""
    kind: str                  # tool_call | tool_result | text | error | done
    name: str = ""
    payload: Any = None


SYSTEM_PROMPT = """\
You are a London office market analyst assisting a commercial real estate team.

HARD RULES ON NUMBERS. These are the product.
1. Every figure you state must come from a tool call. Never recall a number from
   memory, never estimate, never interpolate between figures.
2. State the source and as-of date with any figure from the fact store.
3. If the tools do not have something, say plainly that you do not have it and
   name what you would need. Do not guess. A clean "I don't have that" is a
   correct answer and is far more valuable than a plausible invention.
4. Figures from web search are live and less certain than the fact store. Label
   them as coming from a web search, and never present them as store figures.

DECISIONS. Close on one of: regear, refurbish, re-price, hold, defer capex,
start the conversation. Never close with "monitor" or "keep an eye on it" --
those let a paragraph end without deciding anything, which is the failure this
tool exists to prevent. "Hold" is a real answer when you say why.

When get_signals already gives you why_each_asset and recommended_action for a
building, use those figures and that verb as they stand. They were computed in
Python from the fact store and they are what the brief on screen already says.
Recomputing or rephrasing them produces a second, different number for the same
thing, in front of a reader who can see both.

STYLE. You are talking to a busy professional who will repeat what you say in a
client meeting. Lead with the number and what it means. Be concise. No preamble,
no "great question", no bullet-point dumps unless asked. Two or three sentences
usually does it.

CONTEXT. The fact store holds Savills Central London Office Market Watch data.
The user's watchlist holds their own assets, which may be empty. When a market
fact touches one of their assets, say so explicitly, because that is the whole
point of the tool.
"""


# --------------------------------------------------------------------------
# Tool declarations
# --------------------------------------------------------------------------

def _declarations() -> list[types.FunctionDeclaration]:
    S, T = types.Schema, types.Type
    return [
        types.FunctionDeclaration(
            name="get_metric",
            description=(
                "Look up one market metric for one submarket from the Savills "
                "fact store. Returns value, unit, year-on-year and quarter-on-"
                "quarter change where published, plus source and as-of date."
            ),
            parameters=S(type=T.OBJECT, properties={
                "metric": S(type=T.STRING, description="e.g. vacancy_rate, prime_rent, grade_a_rent_avg, take_up, completions_forecast"),
                "submarket": S(type=T.STRING, description="e.g. City, West End, City Core, City Fringe, Central London"),
                "period": S(type=T.STRING, description="Optional, e.g. 2026Q2 or 2026H1. Omit for the latest."),
                "sector": S(type=T.STRING, description="Optional. Exactly one of the names list_available returns under 'sectors', e.g. 'Tech & Media'. Omit for the all-sector total. Never invent a sector name."),
            }, required=["metric", "submarket"]),
        ),
        types.FunctionDeclaration(
            name="list_available",
            description=(
                "Every metric, submarket, sector and event type the store holds. "
                "Call this first if unsure whether a figure exists. A sector listed "
                "here can be passed to get_metric."
            ),
            parameters=S(type=T.OBJECT, properties={}),
        ),
        types.FunctionDeclaration(
            name="get_signals",
            description=(
                "The currently detected market signals, already ranked, each with "
                "severity, evidence and which of the user's assets it affects."
            ),
            parameters=S(type=T.OBJECT, properties={}),
        ),
        types.FunctionDeclaration(
            name="find_market_activity",
            description=(
                "Named transactions and events from the Savills report: who "
                "took space, what completed, what broke ground, what sold. "
                "Use this whenever the user asks who is active, who is "
                "moving, what deals happened, or asks for concrete examples."
            ),
            parameters=S(type=T.OBJECT, properties={
                "type": S(type=T.STRING, description="letting, completion, development_start or investment. Omit for all."),
                "sector": S(type=T.STRING, description="e.g. AI, Creative, Insurance & Financial, Serviced Office"),
                "submarket": S(type=T.STRING, description="e.g. West End, City, Mayfair. Matches deals anywhere inside it. Most events carry no submarket in this source, so a filtered count is a floor, not a total."),
                "min_sqft": S(type=T.INTEGER, description="Only deals at least this large"),
            }),
        ),
        types.FunctionDeclaration(
            name="get_watchlist",
            description=("The user's own assets: submarket, grade, size, lease "
                         "expiry, break date, passing rent and EPC rating. The "
                         "holdings are illustrative and labelled fictional; the "
                         "market figures they are compared against are not."),
            parameters=S(type=T.OBJECT, properties={}),
        ),
    ]


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------

class Agent:
    def __init__(self, store: Store, watchlist: Watchlist,
                 api_key: str | None = None, model: str | None = None,
                 thinking: str | None = None):
        self.store = store
        self.watchlist = watchlist
        self.model = model or os.getenv("CRE_MODEL", "gemini-3.7-flash")
        self.thinking = thinking or os.getenv("CRE_THINKING", "medium")
        key = api_key or os.getenv("GOOGLE_API_KEY")
        # Explicit key: a bare Client() also honours GEMINI_API_KEY and
        # GOOGLE_GENAI_USE_VERTEXAI, which makes credential bugs baffling.
        self.client = genai.Client(api_key=key) if key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    # -- tools ------------------------------------------------------------

    def _run_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "get_metric":
                asked = args["submarket"]
                f = self.store.get(args["metric"], asked, args.get("period"),
                                   sector=args.get("sector"), climb=True)
                if not f:
                    # A typo and a real data gap need different answers. Only
                    # the second one is a correct "I don't have that".
                    if self.store.resolve_submarket(asked) is None:
                        return {"found": False,
                                "message": f"{asked!r} is not a submarket in this "
                                           f"vocabulary. Call list_available."}
                    return {"found": False,
                            "message": f"{asked} is a recognised submarket, but this "
                                       f"source publishes no {args['metric']} for it "
                                       f"or for any submarket containing it."}
                out = {
                    "found": True,
                    "metric": f.metric,
                    "submarket": f.submarket,
                    "asked_about": asked,
                    "sector": f.sector or "all sectors",
                    "period": str(f.period),
                    "value": f.render_value(),
                    "value_is_published": f.value is not None,
                    "source": f.source.cite(),
                    "url": f.source.url,
                }
                for kind, label in (("yoy", "year_on_year"), ("qoq", "quarter_on_quarter"),
                                    ("vs_avg", "vs_long_run_average")):
                    if d := f.delta(kind):
                        out[label] = d.render()
                if f.note:
                    out["note"] = f.note
                if not self.store._same_submarket(f.submarket, asked):
                    out["broader_geography"] = (
                        f"This source does not break out {asked}. The figure "
                        f"above is for {f.submarket}, which contains it. Say so.")
                return out

            if name == "list_available":
                return {"metrics": self.store.metrics(),
                        "submarkets": self.store.submarkets(),
                        "event_types": self.store.event_types(),
                        "sectors": self.store.sectors(),
                        "as_of": self.store.as_of()}

            if name == "find_market_activity":
                clean = {k: v for k, v in args.items() if v not in (None, "")}
                hits = self.store.find_events(**clean)
                out = {"count": len(hits),
                       "events": [{k: v for k, v in e.items() if k != "_source"}
                                  | {"source": e["_source"].cite()}
                                  for e in hits]}
                if clean.get("submarket"):
                    # Silence here is the bug: 13 of 17 events carry no
                    # submarket, so a filtered list that does not say so reads
                    # as complete when it is a floor.
                    missing = self.store.events_missing_submarket()
                    if missing:
                        out["coverage_caveat"] = (
                            f"{missing} of {len(self.store.events)} events in this "
                            f"source carry no submarket, so they cannot match any "
                            f"submarket filter. State this limitation with the answer.")
                return out

            if name == "get_signals":
                return {"signals": [
                    {"severity": s.severity, "headline": s.headline,
                     "detail": s.detail, "affects_your_assets": s.affected,
                     # Already computed, already sourced, already carrying the
                     # decision verb. Restating either differently is the one
                     # way this loop can contradict the page above it.
                     "why_each_asset": s.match_reasons,
                     "recommended_action": s.match_actions,
                     "sources": s.citations()}
                    for s in detect_all(self.store, self.watchlist)
                ]}

            if name == "get_watchlist":
                return {"label": self.watchlist.label,
                        "count": len(self.watchlist),
                        "fictional": True,
                        "assets": [{"name": a.name, "submarket": a.submarket,
                                    "grade": a.grade, "sqft": a.sqft,
                                    "lease_expiry": a.lease_expiry,
                                    "break_date": a.break_date,
                                    "passing_rent_psf": a.passing_rent_psf,
                                    "epc_rating": a.epc_rating}
                                   for a in self.watchlist.assets]}

            return {"error": f"unknown tool {name}"}

        except AmbiguousQuery as e:
            return {"error": "ambiguous", "message": str(e)}
        except Exception as e:  # tool errors must reach the model, not crash the app
            return {"error": type(e).__name__, "message": str(e)}

    # -- loop -------------------------------------------------------------

    def ask(self, question: str, history: list | None = None) -> Iterator[Event]:
        if not self.enabled:
            yield Event("error", payload="No GOOGLE_API_KEY set, so chat is disabled. "
                                         "The brief and signals above are unaffected.")
            return

        contents = list(history or [])
        contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[
                types.Tool(function_declarations=_declarations()),
                types.Tool(google_search=types.GoogleSearch()),
            ],
            # Required to mix our declarations with the built-in search tool.
            tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking),
        )

        for _ in range(MAX_TURNS):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=contents, config=config)
            except Exception as e:
                yield Event("error", payload=_friendly(e))
                return

            cand = resp.candidates[0] if resp.candidates else None
            parts = list(getattr(cand.content, "parts", None) or []) if cand else []
            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

            if not calls:
                text = resp.text or ""
                # The model's own words must enter history: the tool trace
                # alone is not the answer, and a follow-up ("why?") otherwise
                # reaches a model that never saw what it said. cand.content
                # goes back verbatim, thought signatures and all.
                if cand is not None and cand.content is not None:
                    contents.append(cand.content)
                elif text:
                    contents.append(types.Content(
                        role="model", parts=[types.Part(text=text)]))
                yield Event("text", payload=text)
                yield Event("done", payload={"text": text,
                                             "citations": _citations(cand),
                                             "contents": contents})
                return

            contents.append(cand.content)
            responses = []
            for call in calls:
                args = dict(call.args or {})
                yield Event("tool_call", name=call.name, payload=args)
                result = self._run_tool(call.name, args)
                yield Event("tool_result", name=call.name, payload=result)
                responses.append(types.Part.from_function_response(
                    name=call.name, response=result))
            contents.append(types.Content(role="user", parts=responses))

        yield Event("error", payload=f"Stopped after {MAX_TURNS} tool rounds without "
                                     f"a final answer.")


def _citations(cand) -> list[dict]:
    meta = getattr(cand, "grounding_metadata", None) if cand else None
    chunks = getattr(meta, "grounding_chunks", None) or [] if meta else []
    out = []
    for c in chunks:
        web = getattr(c, "web", None)
        if web:
            out.append({"title": getattr(web, "title", ""), "uri": getattr(web, "uri", "")})
    return out


def _friendly(e: Exception) -> str:
    """Problem, cause, fix. Never a traceback in front of a business user."""
    msg = str(e)
    if "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return ("Your Google API key was rejected. Check GOOGLE_API_KEY in .env, "
                "or get a new one at https://aistudio.google.com/apikey")
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        return ("Gemini rate limit reached. Wait about a minute and try again. "
                "The brief and signals above still work, they need no API.")
    if "NOT_FOUND" in msg or "404" in msg:
        return ("That model is not available on your key. Run "
                "`uv run python scripts/probe_models.py` to see what is, then set "
                "CRE_MODEL in .env.")
    if "DeadlineExceeded" in msg or "timeout" in msg.lower():
        return "The request timed out. Try again, or set CRE_THINKING=low in .env."
    return f"The model call failed: {msg[:200]}"

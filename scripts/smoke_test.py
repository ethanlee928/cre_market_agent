"""Hello world + the architecture gate.

Four checks, each independent so a failure in one still reports the others:

  1. Plain generation                  -> is the key and model working at all?
  2. Function calling alone            -> can the agent call OUR tools?
  3. Google Search grounding alone     -> can it read live news with citations?
  4. BOTH IN ONE REQUEST               -> THE GATE.

Check 4 is the one that matters. The design has news_scan (grounding) and the
streamed tool loop (function calling) running in the same agent turn. On some
providers those two are mutually exclusive. If 4 fails, the architecture needs
a two-call split and it is much cheaper to learn that now than on Sunday.

    uv run python scripts/smoke_test.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    sys.exit("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")

MODEL = os.getenv("CRE_MODEL", "gemini-3.7-flash")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

# Pass the key explicitly. A bare Client() also honours GEMINI_API_KEY and
# GOOGLE_GENAI_USE_VERTEXAI, which makes "why is it using the wrong creds"
# a confusing 20 minutes.
client = genai.Client(api_key=API_KEY)

# A stand-in for the real market_data skill: same shape, hardcoded answer.
get_market_metric = types.FunctionDeclaration(
    name="get_market_metric",
    description=(
        "Look up a London office market metric for a submarket from the "
        "in-house fact store. Returns the value, unit, source and as-of date."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "metric": types.Schema(
                type=types.Type.STRING,
                description="e.g. vacancy_rate, prime_rent, take_up",
            ),
            "submarket": types.Schema(
                type=types.Type.STRING,
                description="e.g. City, West End, City Fringe",
            ),
        },
        required=["metric", "submarket"],
    ),
)

FAKE_STORE = {
    ("vacancy_rate", "City"): {
        "value": 7.0,
        "unit": "pct",
        "source": "Savills Central London Office Market Watch Q2 2026",
        "as_of": "2026-08-06",
    }
}

results: dict[str, tuple[bool, str]] = {}


def record(name: str, ok: bool, detail: str) -> None:
    results[name] = (ok, detail)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"        {detail}\n")


def parts_of(resp):
    """Content parts of the first candidate, or [] if the response is empty."""
    if not getattr(resp, "candidates", None):
        return []
    content = resp.candidates[0].content
    return list(getattr(content, "parts", None) or [])


def function_calls_in(resp):
    return [p.function_call for p in parts_of(resp) if getattr(p, "function_call", None)]


print(f"\nModel: {MODEL}\n{'=' * 70}\n")

# --- 1. Plain generation ------------------------------------------------
print("[1] Plain generation")
try:
    r = client.models.generate_content(
        model=MODEL,
        contents="Reply with exactly: hello from the London office market agent",
    )
    record("plain generation", bool(r.text), (r.text or "<empty>").strip()[:120])
except Exception as e:
    record("plain generation", False, f"{type(e).__name__}: {e}")

# --- 2. Function calling alone ------------------------------------------
print("[2] Function calling alone")
try:
    r = client.models.generate_content(
        model=MODEL,
        contents="What is the office vacancy rate in the City right now?",
        config=types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[get_market_metric])],
        ),
    )
    calls = function_calls_in(r)
    record(
        "function calling alone",
        bool(calls),
        f"tool calls: {[(c.name, dict(c.args)) for c in calls]}" if calls
        else f"no function_call returned. text={(r.text or '')[:100]!r}",
    )
except Exception as e:
    record("function calling alone", False, f"{type(e).__name__}: {e}")

# --- 3. Google Search grounding alone -----------------------------------
print("[3] Google Search grounding alone")
try:
    r = client.models.generate_content(
        model=MODEL,
        contents="Any London office leasing news in the last month? One sentence.",
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    meta = getattr(r.candidates[0], "grounding_metadata", None) if r.candidates else None
    chunks = getattr(meta, "grounding_chunks", None) or [] if meta else []
    record(
        "search grounding alone",
        bool(r.text),
        f"{len(chunks)} grounding source(s). {(r.text or '')[:130].strip()}",
    )
except Exception as e:
    record("search grounding alone", False, f"{type(e).__name__}: {e}")

# --- 4. THE GATE: both tool types in one request ------------------------
print("[4] THE GATE: grounding + function declarations in ONE request")
try:
    r = client.models.generate_content(
        model=MODEL,
        contents=(
            "Two things. First look up the City vacancy rate using the "
            "get_market_metric tool. Then search the web for recent London "
            "office leasing news."
        ),
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(function_declarations=[get_market_metric]),
                types.Tool(google_search=types.GoogleSearch()),
            ],
            # Required to mix a built-in server-side tool (google_search) with
            # our own function declarations in one request. Without it the API
            # returns 400 INVALID_ARGUMENT naming this exact field.
            tool_config=types.ToolConfig(
                include_server_side_tool_invocations=True,
            ),
        ),
    )
    calls = function_calls_in(r)
    record(
        "grounding + functions together",
        True,
        f"ACCEPTED. tool calls={[c.name for c in calls]}; "
        f"text={(r.text or '')[:90].strip()!r}",
    )
except Exception as e:
    record(
        "grounding + functions together",
        False,
        f"{type(e).__name__}: {str(e)[:400]}",
    )

# --- verdict ------------------------------------------------------------
print("=" * 70)
for name, (ok, _) in results.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")

gate_ok = results.get("grounding + functions together", (False, ""))[0]
print("\n" + "=" * 70)
if gate_ok:
    print("GATE PASSED. One agent turn can hold both our tools and live search.")
    print("Build the single-loop design as planned.")
else:
    print("GATE FAILED. Grounding and function declarations cannot share a request.")
    print("Architecture change: split into two calls. news_scan runs as its own")
    print("grounded call, its result is injected as context, and the tool loop")
    print("runs separately. Roughly +1h, and far cheaper to know now.")
print("=" * 70)

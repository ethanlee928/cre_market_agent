"""The agent loop, driven by a fake client. No key, no network.

The loop half of the system was the untested half, and it held the one bug the
review confirmed: ask() never appended the model's final text to `contents`, so
st.session_state.history carried every user question and all tool traffic but
none of the assistant's answers. A follow-up like "why do you say that?"
reached a model that had never seen what it said.

The fake returns real google.genai.types objects, so what round-trips here is
what round-trips in production, thought signatures and all. Only the network
is faked.

Run:  uv run python tests/test_agent_loop.py     (or pytest)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types

from evals import graders

from cre_agent.llm import gemini
from cre_agent.llm.gemini import Agent
from cre_agent.store import Fact, Period, Source, Store
from cre_agent.watchlist import Watchlist


# -- fakes ------------------------------------------------------------------

def text_resp(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]))])


def call_resp(name: str, **args) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(name=name, args=args))]))])


class FakeModels:
    def __init__(self, responses):
        self.queue = list(responses)
        self.calls: list[list] = []       # contents snapshot per request

    def generate_content(self, model, contents, config):
        self.calls.append(list(contents))
        if not self.queue:
            raise AssertionError("fake client exhausted: loop asked for more "
                                 "turns than the test queued")
        return self.queue.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def agent_with(responses) -> Agent:
    a = Agent(Store.load(), Watchlist.load(), api_key=None)
    a.client = FakeClient(responses)
    return a


def run(agent: Agent, question: str, history=None):
    events = list(agent.ask(question, history))
    done = [e for e in events if e.kind == "done"]
    return events, (done[0].payload if done else None)


# -- the history bug --------------------------------------------------------

def test_final_answer_enters_history():
    """The regression test. Failed against the code the review read."""
    answer = "City vacancy is 9.1% (Savills, as of 2026-08-06)."
    agent = agent_with([
        call_resp("get_metric", metric="vacancy_rate", submarket="City"),
        text_resp(answer),
    ])
    _, done = run(agent, "What's the vacancy rate in the City?")
    last = done["contents"][-1]
    assert last.role == "model", f"history ends with {last.role!r}, not the answer"
    assert last.parts[0].text == answer


def test_tool_round_trip_enters_history():
    agent = agent_with([
        call_resp("get_metric", metric="vacancy_rate", submarket="City"),
        text_resp("done"),
    ])
    _, done = run(agent, "vacancy?")
    contents = done["contents"]
    assert len(contents) == 4, [c.role for c in contents]
    assert contents[0].role == "user" and contents[0].parts[0].text == "vacancy?"
    assert contents[1].parts[0].function_call.name == "get_metric"
    assert contents[2].parts[0].function_response is not None
    assert contents[3].role == "model" and contents[3].parts[0].text == "done"


def test_incoming_history_is_not_mutated():
    earlier = [types.Content(role="user", parts=[types.Part(text="earlier turn")])]
    agent = agent_with([text_resp("ok")])
    _, done = run(agent, "next question", history=earlier)
    assert len(earlier) == 1, "ask() mutated the caller's history list"
    assert done["contents"][0] is earlier[0]
    assert len(done["contents"]) == 3   # earlier + question + answer


def test_max_turns_exhaustion_reports_not_loops():
    agent = agent_with(
        [call_resp("list_available")] * gemini.MAX_TURNS)
    events, done = run(agent, "loop forever")
    assert done is None
    assert events[-1].kind == "error"
    assert str(gemini.MAX_TURNS) in events[-1].payload
    assert len(agent.client.models.calls) == gemini.MAX_TURNS


def test_missing_key_yields_error_not_traceback():
    """Automates the hand-check that used to live in CLAUDE.md."""
    saved = {k: os.environ.pop(k, None)
             for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY")}
    try:
        agent = Agent(Store.load(), Watchlist.load(), api_key=None)
        assert agent.enabled is False
        events = list(agent.ask("anything"))
        assert len(events) == 1 and events[0].kind == "error"
        assert "GOOGLE_API_KEY" in events[0].payload
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# -- _run_tool: the dispatch the model actually talks to --------------------

def tool_agent() -> Agent:
    return Agent(Store.load(), Watchlist.load(), api_key=None)


def test_unknown_tool_is_an_error_dict():
    out = tool_agent()._run_tool("frobnicate", {})
    assert out == {"error": "unknown tool frobnicate"}


def test_typo_and_data_gap_get_different_answers():
    agent = tool_agent()
    typo = agent._run_tool("get_metric",
                           {"metric": "vacancy_rate", "submarket": "Basingstoke"})
    gap = agent._run_tool("get_metric",
                          {"metric": "hybrid_working_share", "submarket": "Paddington"})
    assert typo["found"] is False and "not a submarket" in typo["message"]
    assert gap["found"] is False and "recognised submarket" in gap["message"]


def test_get_metric_always_carries_source_and_period():
    out = tool_agent()._run_tool("get_metric",
                                 {"metric": "vacancy_rate", "submarket": "City"})
    assert out["found"] is True
    assert "Savills" in out["source"] and out["period"]


def test_hierarchy_climb_labels_the_broader_geography():
    out = tool_agent()._run_tool("get_metric",
                                 {"metric": "grade_a_rent_avg", "submarket": "Mayfair"})
    assert out["found"] is True
    assert out["submarket"] == "West End" and out["asked_about"] == "Mayfair"
    assert "West End" in out["broader_geography"]


def test_submarket_filtered_events_carry_the_coverage_caveat():
    out = tool_agent()._run_tool("find_market_activity", {"submarket": "West End"})
    assert "coverage_caveat" in out, "a filtered count must say it is a floor"


def test_ambiguous_lookup_surfaces_as_error_not_crash():
    src = Source("T", "t", "2026-01-01", "u")
    p = Period.parse("2026Q2")
    store = Store([Fact("m", "City", p, 1.0, "pct", src),
                   Fact("m", "City", p, 2.0, "pct", src)], [], [src])
    agent = Agent(store, Watchlist.load(), api_key=None)
    out = agent._run_tool("get_metric", {"metric": "m", "submarket": "City"})
    assert out["error"] == "ambiguous" and "disambiguate" in out["message"]


# -- the eval graders: offline, so the live harness can be trusted ----------

def test_grader_million_expansion_matches_full_form():
    allowed = graders.allowed_figures([], [{"value": "7,700,000 sq ft"}])
    fails, _ = graders.figures_sourced(
        "Completions are heading for 7.7m sq ft.", allowed, has_citations=False)
    assert fails == [], fails


def test_grader_fabricated_figure_is_named():
    allowed = graders.allowed_figures([], [{"value": "9.1%"}])
    fails, _ = graders.figures_sourced(
        "Vacancy is 9.1% and rents hit £99.99 psf.", allowed, has_citations=False)
    assert len(fails) == 1 and "99.99" in fails[0], fails


def test_grader_years_periods_and_dates_are_not_figures():
    stated = graders.stated_figures(
        "In 2026Q2, as of 2026-08-06, vacancy was 9.1% against the 2026 forecast.")
    assert [v for _, v in stated] == [9.1], stated


def test_grader_small_counts_are_not_figures_but_41_is():
    assert graders.stated_figures("3 of 5 signals fired.") == []
    stated = graders.stated_figures("41 requirements chasing 21 options.")
    assert {v for _, v in stated} == {41.0, 21.0}, stated


def test_grader_postcodes_and_ordinals_are_not_figures():
    """E14 failed an honest refusal in the first live run. Never again."""
    assert graders.stated_figures("leasing data for E14 or NW10") == []
    assert graders.stated_figures("refurbish the 14th and 15th floors") == []


def test_grader_rounded_restatement_is_sourced():
    allowed = graders.allowed_figures([], [{"detail": "worth £919,980 a year"}])
    fails, _ = graders.figures_sourced(
        "That is roughly £920,000 a year.", allowed, has_citations=False)
    assert fails == [], fails


def test_grader_web_citation_downgrades_to_warning():
    fails, warns = graders.figures_sourced(
        "The Bank Rate is 4.0%.", allowed=set(), has_citations=True)
    assert fails == [] and len(warns) == 1


def test_grader_action_verb_uses_the_closed_vocabulary():
    assert graders.action_verb("I would regear the lease now.")
    assert graders.action_verb("Re-price it against the Grade A average.")
    assert not graders.action_verb("Keep monitoring the situation.")
    assert graders.forbidden("Keep monitoring it.", [r"\bmonitor"]) == [r"\bmonitor"]


def test_grader_called_matches_args_and_alternatives():
    trace = [("get_metric", {"metric": "active_demand",
                             "submarket": "Central London"})]
    assert graders.called(trace, {"tool": "get_metric",
                                  "args_include": {"metric": "active_demand"},
                                  "args_absent": ["sector"]})
    assert graders.called(trace, {"tool": "get_signals|get_metric"})
    assert not graders.called(trace, {"tool": "get_metric",
                                      "args_include": {"sector": "Tech & Media"}})


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

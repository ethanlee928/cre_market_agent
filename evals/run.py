"""Live evals for the agent loop: the half of the system a test cannot pin.

The brief's figures are computed in Python and asserted by tests/. The chat
loop's figures are produced by a model that is only *asked* to behave, so this
harness measures it: does every stated figure trace to tool traffic, does the
model refuse what it does not have, does it call the tools a question needs,
does it close on the decision vocabulary and never on "monitor".

Needs GOOGLE_API_KEY (tests/ stays key-free on purpose). Each rep is a live,
non-deterministic model call: a case fails the run only when it fails a
majority of its reps, so one flaky rep does not cry regression and a real one
cannot hide. Full transcripts land in evals/runs/ for diffing across prompt or
model changes.

Run:  uv run python evals/run.py [--n 3] [--case id]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import yaml
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from cre_agent.llm.gemini import Agent            # noqa: E402
from cre_agent.store import Store                 # noqa: E402
from cre_agent.watchlist import SubmarketIndex, Watchlist  # noqa: E402
from evals import graders                         # noqa: E402

CASES = Path(__file__).parent / "cases.yaml"
RUNS = Path(__file__).parent / "runs"


def run_case(agent: Agent, case: dict) -> dict:
    """One rep: drive ask() exactly as app.py does, record everything."""
    trace, results, citations = [], [], []
    answer, error, history = "", None, None
    questions = [case["question"]]
    if case.get("follow_up"):
        questions.append(case["follow_up"])
    for q in questions:
        answer, error = "", None
        for ev in agent.ask(q, history):
            if ev.kind == "tool_call":
                trace.append((ev.name, dict(ev.payload)))
            elif ev.kind == "tool_result":
                results.append(ev.payload)
            elif ev.kind == "text":
                answer = ev.payload
            elif ev.kind == "done":
                citations.extend(ev.payload.get("citations", []))
                history = ev.payload.get("contents")
            elif ev.kind == "error":
                error = ev.payload
        if error:
            break
    return {"answer": answer, "error": error, "trace": trace,
            "results": results, "citations": citations,
            "questions": questions}


def grade(case: dict, out: dict) -> tuple[list[str], list[str]]:
    """(failures, warnings) for one rep."""
    exp = case.get("expect", {})
    fails, warns = [], []
    if out["error"]:
        return [f"loop error: {out['error']}"], warns

    answer, trace = out["answer"], out["trace"]
    for m in exp.get("must_call", []):
        if not graders.called(trace, m):
            fails.append(f"missing tool call: {m}")
    for m in exp.get("must_not_call", []):
        if graders.called(trace, m):
            fails.append(f"forbidden tool call: {m}")
    if exp.get("action_verb") and not graders.action_verb(answer):
        fails.append("no decision verb from ACTIONS in the answer")
    if hit := graders.forbidden(answer, exp.get("forbid", [])):
        fails.append(f"forbidden phrasing: {hit}")
    if missed := graders.required(answer, exp.get("answer_matches", [])):
        fails.append(f"answer does not match: {missed}")

    # refusal_or_web is figures_sourced by another name, kept distinct in the
    # case file because the intent differs: "answer from the store" vs "either
    # refuse or answer from the web, labelled". The arithmetic is the same --
    # a stated figure needs a tool source, or a citation to hide behind.
    if exp.get("figures_sourced") or exp.get("refusal_or_web"):
        allowed = graders.allowed_figures(trace, out["results"],
                                          " ".join(out["questions"]))
        f, w = graders.figures_sourced(answer, allowed, bool(out["citations"]))
        if f:
            fails.append(f"unsourced figures: {f}")
        warns.extend(f"web-cited, unverifiable here: {t}" for t in w)
    return fails, warns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=1, help="reps per case")
    ap.add_argument("--case", help="run only this case id")
    args = ap.parse_args()

    # GOOGLE_API_KEY only: Agent.__init__ reads that one name, deliberately
    # (a bare Client() would also honour GEMINI_API_KEY). Accepting the other
    # spelling here passes the guard and then fails every case with "no
    # GOOGLE_API_KEY set" from inside the loop.
    if not os.getenv("GOOGLE_API_KEY"):
        print("No GOOGLE_API_KEY set, so the live evals cannot run. Add one to "
              ".env — the offline suite (tests/) needs no key.")
        return 1

    cases = yaml.safe_load(CASES.read_text())["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"no case with id {args.case!r} in {CASES.name}")
            return 1

    store, index = Store.load(), SubmarketIndex.load()
    agents = {
        "default": Agent(store, Watchlist.load()),
        "empty": Agent(store, Watchlist([], index)),
    }

    transcript, hard_failed = [], []
    for case in cases:
        agent = agents["empty" if case.get("watchlist") == "empty" else "default"]
        reps = []
        for rep in range(args.n):
            out = run_case(agent, case)
            fails, warns = grade(case, out)
            reps.append({"rep": rep + 1, "fails": fails, "warns": warns, **out})
        transcript.append({"id": case["id"], "question": case["question"],
                           "reps": reps})

        ok = sum(1 for r in reps if not r["fails"])
        failed = len(reps) - ok > len(reps) // 2   # majority rule
        if failed:
            hard_failed.append(case["id"])
        print(f"  {'FAIL' if failed else 'PASS'}  {ok}/{len(reps)}  {case['id']}")
        for r in reps:
            for f in r["fails"]:
                print(f"          rep {r['rep']}: {f}")
            for w in r["warns"]:
                print(f"          rep {r['rep']} (warn): {w}")

    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(transcript, indent=2, default=str))

    print(f"\n{len(cases) - len(hard_failed)}/{len(cases)} cases passed "
          f"({args.n} rep{'s' if args.n > 1 else ''} each) · transcript: {path}")
    if hard_failed:
        print(f"failed: {', '.join(hard_failed)}")
    return 1 if hard_failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""London Office Market Monitor — the app a non-technical CRE user actually opens.

One chat surface. The brief arrives as the first message, so value lands before
anyone types (premise P1). The watchlist sits in the sidebar (premise P2). Every
figure carries its source and as-of date (premise P3).

Run:  uv run streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent / "src"))
load_dotenv()

from cre_agent.llm.gemini import Agent            # noqa: E402
from cre_agent.signals import detect_all           # noqa: E402
from cre_agent.store import Store                  # noqa: E402
from cre_agent.watchlist import Watchlist          # noqa: E402

st.set_page_config(page_title="London Office Market Monitor",
                   page_icon="🏢", layout="centered")

BADGE = {"RISK": "🔴", "WATCH": "🟠", "OPPORTUNITY": "🟢"}


# --------------------------------------------------------------------------
# Cached resources. Session state schema is declared here, not discovered
# later, because Streamlit reruns the whole script on every interaction and
# anything not in session_state is wiped.
# --------------------------------------------------------------------------

@st.cache_resource
def load_all():
    store = Store.load()
    watchlist = Watchlist.load()
    return store, watchlist, detect_all(store, watchlist)


store, watchlist, signals = load_all()
agent = Agent(store, watchlist)

st.session_state.setdefault("messages", [])   # [{role, content, tools?, citations?}]
st.session_state.setdefault("history", [])    # google-genai Content list


# --------------------------------------------------------------------------
# Sidebar: the watchlist and the provenance
# --------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Your portfolio")
    if len(watchlist):
        st.caption(watchlist.label)
        for a in watchlist.assets:
            hits = [s for s in signals if a.name in s.affected]
            worst = min((s.severity for s in hits),
                        key=lambda s: ["RISK", "WATCH", "OPPORTUNITY"].index(s),
                        default=None)
            st.markdown(f"**{BADGE.get(worst, '⚪')} {a.name}**  \n"
                        f"<span style='color:#888;font-size:0.85em'>{a.describe()}</span>",
                        unsafe_allow_html=True)
    else:
        # Designed zero-state, not an accident. Market-wide must work fully.
        st.info("No assets yet. You're seeing the market-wide view.\n\n"
                "Add yours in `config/watchlist.yaml` to filter signals to "
                "the buildings you actually hold.")

    st.divider()
    st.caption(f"**Data as of {store.as_of()}**")
    for s in store.sources:
        st.caption(f"{s.publisher}: {s.title}")
    st.caption(f"{len(store.facts)} facts · {len(store.events)} events")

    if not agent.enabled:
        st.warning("No `GOOGLE_API_KEY`, so chat is off. Add one to `.env` to "
                   "enable it.")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.history = []
        st.rerun()


# --------------------------------------------------------------------------
# The brief, as the first message in the conversation
# --------------------------------------------------------------------------

st.title("🏢 London Office Market Monitor")

with st.chat_message("assistant"):
    n = len([s for s in signals if s.affected])
    if len(watchlist):
        st.markdown(f"Here's what moved this quarter. **{n} of {len(signals)} signals "
                    f"touch your portfolio.**")
    else:
        st.markdown(f"Here's what moved this quarter. **{len(signals)} signals** "
                    f"across Central London.")

    for s in signals:
        with st.expander(f"{BADGE[s.severity]}  **{s.headline}**",
                         expanded=(s.severity == "RISK" and bool(s.affected))):
            st.markdown(s.detail)
            for name in s.affected:
                reason = s.match_reasons.get(name)
                action = s.match_actions.get(name)
                if reason:
                    # The decision verb is rendered here, from Python, so it
                    # is identical on every run and a test can assert what is
                    # in the vocabulary. A model-authored verb could only be
                    # asked to stay inside ACTIONS, never held to it.
                    st.markdown(f"**{name}** — {reason}"
                                + (f"  \n→ **{action}**" if action else ""))
                else:
                    st.markdown(f"**Your exposure:** `{name}`")
            for c in s.citations():
                st.caption(f"Source: {c}")

    # Attribution comes off the newest loaded source, not a literal. With a
    # second quarter merged in, a hardcoded title credits the wrong report.
    newest = store.newest_source()
    st.caption(f"{newest.publisher} {newest.title} · as of {store.as_of()} · "
               f"all figures computed from the fact store, none generated")


# --------------------------------------------------------------------------
# Conversation
# --------------------------------------------------------------------------

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m.get("tools"):
            with st.expander(f"🔧 {len(m['tools'])} tool calls", expanded=False):
                for t in m["tools"]:
                    st.code(t, language="text")
        st.markdown(m["content"])
        for c in m.get("citations", []):
            st.caption(f"🌐 [{c['title']}]({c['uri']})")

# Seeded questions: a blank box is a blank page problem for someone who does
# not yet know what this thing can answer.
if not st.session_state.messages and agent.enabled:
    st.caption("Try:")
    # Two rows of two. Four across is unreadable in a centred layout.
    seeds = ["Should we be worried about the City Fringe?",
             "Who's taking space right now?",
             "What's driving demand right now?",
             "What's happening at Regent Quarter?",
             # The leading demo: building vs building against named peers.
             "Is The Bailey priced right against its peers?",
             "Compare 99 City Road to its neighbours"]
    for i in range(0, len(seeds), 2):
        for col, q in zip(st.columns(2), seeds[i:i + 2]):
            if col.button(q, use_container_width=True):
                st.session_state.pending = q
                st.rerun()

prompt = st.chat_input("Ask anything about the London office market...",
                       disabled=not agent.enabled)
if not prompt and "pending" in st.session_state:
    prompt = st.session_state.pop("pending")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        tools, answer, citations = [], "", []
        # The money shot: the panel watches the agent work instead of a spinner.
        with st.status("Thinking...", expanded=True) as status:
            for ev in agent.ask(prompt, st.session_state.history):
                if ev.kind == "tool_call":
                    line = f"{ev.name}({', '.join(f'{k}={v}' for k, v in ev.payload.items())})"
                    tools.append(line)
                    st.write(f"🔧 `{line}`")
                elif ev.kind == "tool_result":
                    p = ev.payload
                    if isinstance(p, dict) and p.get("found") is False:
                        st.write("   ↳ not in the fact store")
                    elif isinstance(p, dict) and "value" in p:
                        st.write(f"   ↳ {p['value']}  ·  {p.get('period', '')}")
                    else:
                        st.write("   ↳ ok")
                elif ev.kind == "text":
                    answer = ev.payload
                elif ev.kind == "done":
                    citations = ev.payload.get("citations", [])
                    st.session_state.history = ev.payload.get("contents", [])
                elif ev.kind == "error":
                    answer = f"⚠️ {ev.payload}"
            status.update(label=f"Used {len(tools)} tool calls", state="complete",
                          expanded=False)

        st.markdown(answer)
        for c in citations:
            st.caption(f"🌐 [{c['title']}]({c['uri']})")

    st.session_state.messages.append({"role": "assistant", "content": answer,
                                      "tools": tools, "citations": citations})

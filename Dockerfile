# syntax=docker/dockerfile:1

# The uv base image, pinned. python3.12 matches the local .venv (3.12.10) and
# satisfies requires-python = ">=3.11", so the container resolves the same
# wheels as the laptop and a test that passes in one passes in the other.
FROM ghcr.io/astral-sh/uv:0.9.7-python3.12-bookworm-slim

# A non-root user, created before anything is built so the final USER switch
# is the last word. /app stays root-owned and read-only to the app, which is
# all it needs: nothing here writes to disk at run time.
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

WORKDIR /app

# PYTHONUNBUFFERED      — logs reach `docker logs` even if the app dies mid-write.
# UV_COMPILE_BYTECODE   — precompile at build time, so the first page load is
#                         not paying for it and read-only /app never needs to
#                         write __pycache__ at run time.
# UV_LINK_MODE=copy     — the uv cache below is a mount, not a layer; copy out
#                         of it rather than hard-link into it.
# UV_PYTHON_DOWNLOADS=0 — use the interpreter the base image already ships.
# UV_NO_DEV             — no dev group today; keeps one out of the image if
#                         one is ever added.
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1

# Dependencies first, resolved from the lockfile alone. This layer rebuilds
# only when pyproject.toml or uv.lock changes — editing app.py does not
# re-resolve streamlit and google-genai.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Then the source, and the project itself.
#
# The whole repo goes to one directory, and that is load-bearing.
# store.DATA_DIR and submarkets.CONFIG_DIR are both `parents[2]` of the module
# file, so cre_agent must be imported from /app/src for them to resolve to
# /app/data and /app/config. Two things guarantee it: uv installs the project
# editable, and app.py (like every test) puts ./src on sys.path first. Split
# the repo across directories here and every fact lookup 404s.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Put the venv's executables first, so `streamlit` and `python` are the
# project's without going through `uv run`.
ENV PATH="/app/.venv/bin:$PATH"

# The uv image sets ENTRYPOINT ["/usr/local/bin/uv"]. Reset it so CMD — and
# `docker run <image> <cmd>` — mean what they say.
ENTRYPOINT []

# Docker does not reliably set HOME from /etc/passwd. Streamlit touches
# ~/.streamlit, and an unwritable HOME would surface as an opaque failure
# given the committed showErrorDetails = "none".
ENV HOME=/home/nonroot

USER nonroot

EXPOSE 8501

# Streamlit's own readiness endpoint. No curl in a slim image, so ask Python.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)" || exit 1

# --server.address is not optional. Committed .streamlit/config.toml pins
# address = "localhost", which inside a container binds loopback only and makes
# `-p 8501:8501` publish a port that nothing answers on. A CLI flag outranks
# the config file in Streamlit's precedence, so the host default is left alone
# and only the container overrides it.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501"]

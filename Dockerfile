FROM ghcr.io/astral-sh/uv:0.9.7-python3.12-bookworm-slim

# A non-root user, created before anything is built so the final USER switch
# is the last word. /app stays root-owned and read-only to the app, which is
# all it needs: nothing here writes to disk at run time.
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

ENV PATH="/app/.venv/bin:$PATH"

# The uv image sets ENTRYPOINT ["/usr/local/bin/uv"]. Reset it so CMD — and
# `docker run <image> <cmd>` — mean what they say.
ENTRYPOINT []

ENV HOME=/home/nonroot
USER nonroot

EXPOSE 8501

# Streamlit's own readiness endpoint. No curl in a slim image, so ask Python.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)" || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501"]

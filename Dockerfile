# Review UI + CLI in one image. Secrets come from the environment at run time, never from the image.
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --extra langfuse
COPY src ./src
COPY migrations ./migrations
COPY prompts ./prompts
COPY profiles ./profiles
RUN uv sync --locked --no-dev --extra langfuse && useradd --create-home signal
USER signal
EXPOSE 8765
HEALTHCHECK CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8765\")}/healthz')"
# SIGNAL_UI_PASSWORD is required for a non-local bind (enforced by `signal serve`)
# Render (and most hosts) pass the port in $PORT
CMD ["sh", "-c", "signal migrate && signal serve --host 0.0.0.0 --port ${PORT:-8765}"]

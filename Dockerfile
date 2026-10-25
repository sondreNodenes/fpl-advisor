FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY fpl_advisor ./fpl_advisor
RUN uv sync --frozen --no-dev

RUN useradd --create-home app
USER app

# Cloud Run sets PORT. The job overrides the command with `python -m fpl_advisor.pipeline`.
CMD ["sh", "-c", "exec uvicorn fpl_advisor.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

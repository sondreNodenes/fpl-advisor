# Two stages: uv builds the virtualenv; the runtime image gets only Python, the venv and
# the app, with no build tools or pip. Base images are pinned by digest (Dependabot bumps).
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY fpl_advisor ./fpl_advisor
# Install the project itself as a regular (non-editable) package inside the venv.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
# pip is not used at runtime and carries its own CVEs; remove it.
RUN python -m pip uninstall --yes --quiet pip && \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
# Owned by root and not writable by the app user; the app only writes to /tmp.
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /tmp
USER 10001

# Cloud Run sets PORT. The job overrides the command with `python -m fpl_advisor.pipeline`.
# Health is checked by Cloud Run's startup probe on /health (Docker HEALTHCHECK is ignored there).
CMD ["sh", "-c", "exec uvicorn fpl_advisor.app:app --host 0.0.0.0 --port ${PORT:-8080}"]

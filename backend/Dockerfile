# syntax=docker/dockerfile:1
# Base images are pinned by digest. Update the tag and the digest together.
FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN groupadd --gid 10001 finvest && useradd --uid 10001 --gid finvest --no-create-home finvest
USER finvest
EXPOSE 8000
# One image, four entry points. The default is the API; Compose overrides the command for the
# worker, scheduler and migrations. Forwarded headers are not trusted here: the proxy in front
# is configured with the deployment.
CMD ["uvicorn", "app.api_main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--timeout-graceful-shutdown", "25"]

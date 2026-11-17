# syntax=docker/dockerfile:1

# ---- build stage: install the package into a virtual environment ------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[bigquery]"

# ---- runtime stage: no build tools, no source tree, non-root user ------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    DUCKDB_PATH=/app/data/warehouse.duckdb \
    SEED_DIR=/app/data/seed

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin api
WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY data/seed ./data/seed

# Build the demo database into the image, so the container needs no writable disk.
RUN python -m marketing_api.seed && chown -R api:api /app
USER api

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=2)"]

# sh -c so $PORT is honoured (Cloud Run sets it); exec makes uvicorn PID 1, so it
# receives SIGTERM and finishes in-flight requests before stopping.
CMD ["sh", "-c", "exec uvicorn marketing_api.main:app_factory --factory --host 0.0.0.0 --port \"$PORT\" --no-access-log"]

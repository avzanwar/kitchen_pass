# Single-image deployment: one container serves the API and the built frontend.
#
# One service means one URL, no CORS, and same-origin WebSockets — which is what
# free hosting tiers make easy. Build context is the repo root.

# ---------------------------------------------------------------------------
# 1. Build the frontend
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web

WORKDIR /build
COPY web/package.json web/package-lock.json* ./web/
RUN cd web && npm ci

COPY web ./web
COPY conformance ./conformance

# An image whose offline engine disagrees with the server engine is worse than
# no image, so conformance gates the build.
RUN cd web && npx vitest run && npm run build

# ---------------------------------------------------------------------------
# 2. Python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    KP_STATIC_DIR=/app/static

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so editing application code does not rebuild this layer.
COPY server/pyproject.toml server/uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev 2>/dev/null \
 || uv sync --no-install-project --no-dev

COPY server/ ./
RUN uv sync --no-dev

COPY --from=web /build/web/dist ./static

RUN useradd --system --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Hosts that inject $PORT (Render, Railway, Koyeb) override it; 8000 otherwise.
# Migrations run in the app's lifespan, so there is nothing to do here first.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

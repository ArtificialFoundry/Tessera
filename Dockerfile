# -- Frontend build -----------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ .
RUN npx tsc --noEmit && npx vite build

# -- Python deps --------------------------------------------------------------
FROM python:3.12-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# -- Runtime ------------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY --from=deps /app/src /app/src
COPY --from=frontend /src/tessera/static/dist /app/src/tessera/static/dist
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd -r -s /usr/sbin/nologin tessera && \
    mkdir -p /var/lib/tessera/backups && \
    chown -R tessera:tessera /var/lib/tessera && \
    chmod -R a+rX /app
USER tessera
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8780/api/v1/ping')"]
EXPOSE 8780
CMD ["uvicorn", "tessera.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8780"]

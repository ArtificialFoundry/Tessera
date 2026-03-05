FROM python:3.12-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY pyproject.toml uv.lock ./
COPY src/ src/
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd -r -s /usr/sbin/nologin tessera
USER tessera
EXPOSE 8780
CMD ["uvicorn", "tessera.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8780"]

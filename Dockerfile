FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

COPY src/ src/

RUN useradd -r -s /usr/sbin/nologin tessera
USER tessera

EXPOSE 8780

CMD ["uv", "run", "uvicorn", "tessera.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8780"]

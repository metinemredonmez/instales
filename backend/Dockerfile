# InstiLens API + worker image. Same image, two commands:
#   api:       instilens api --host 0.0.0.0 --port 8000
#   scheduler: instilens scheduler
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra postgres
COPY . .
RUN uv sync --frozen --no-dev --extra postgres
ENV PATH="/app/.venv/bin:$PATH" INSTILENS_ENVIRONMENT=production
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["instilens", "api", "--host", "0.0.0.0", "--port", "8000"]

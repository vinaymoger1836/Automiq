FROM ghcr.io/astral-sh/uv:0.9-python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY apps/api ./apps/api
COPY alembic.ini ./alembic.ini
COPY infra/migrations ./infra/migrations
COPY scripts/check_phase1_schema.py ./scripts/check_phase1_schema.py
COPY scripts/cleanup_retention.py ./scripts/cleanup_retention.py
ENV PYTHONPATH=/app/apps/api
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

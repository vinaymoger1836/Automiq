FROM ghcr.io/astral-sh/uv:0.9-python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY apps/api/app/config.py ./apps/api/app/config.py
COPY apps/api/app/__init__.py ./apps/api/app/__init__.py
COPY services/orchestrator ./services/orchestrator
COPY scripts/smoke.py ./scripts/smoke.py
ENV PYTHONPATH=/app/apps/api:/app/services/orchestrator
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "orchestrator.worker"]

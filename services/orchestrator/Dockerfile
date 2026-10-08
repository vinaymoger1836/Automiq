FROM ghcr.io/astral-sh/uv:0.9-python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY apps/api/app ./apps/api/app
COPY services/orchestrator ./services/orchestrator
COPY scripts/smoke.py ./scripts/smoke.py
COPY scripts/fake_https_provider.py ./scripts/fake_https_provider.py
ENV PYTHONPATH=/app/apps/api:/app/services/orchestrator
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "orchestrator.worker"]

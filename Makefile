.PHONY: up down smoke migrate schema-check check
up:
	docker compose --env-file .env -f infra/compose.yaml up -d --build

down:
	docker compose --env-file .env -f infra/compose.yaml down

smoke:
	docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py

migrate:
	docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head

schema-check:
	docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev python /app/scripts/check_phase1_schema.py

check:
	uv run ruff check .
	uv run mypy
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck
	npm --prefix apps/web run build

.PHONY: up down smoke check
up:
	docker compose --env-file .env -f infra/compose.yaml up -d --build

down:
	docker compose --env-file .env -f infra/compose.yaml down

smoke:
	docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py

check:
	uv run ruff check .
	uv run mypy
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck
	npm --prefix apps/web run build

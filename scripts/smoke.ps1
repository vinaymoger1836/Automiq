$ErrorActionPreference = 'Stop'
docker compose --progress quiet --env-file .env -f infra/compose.yaml up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$response = Invoke-RestMethod http://localhost:8000/health/ready
if ($response.status -ne 'ok') { throw 'API readiness check failed' }
Write-Output 'Phase 0 smoke passed'

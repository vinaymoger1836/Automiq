# Workflow schema

`graph-v1.schema.json` is generated from the canonical Pydantic `Graph` model in `apps/api/app/graph.py`. Regenerate it from the repository root after changing the contract:

```powershell
$env:PYTHONPATH='apps/api'
uv run --frozen python scripts/export_graph_schema.py
```

The Phase 1 HTTP/PostgreSQL E2E check compares this artifact with the live Pydantic schema and checks the OpenAPI endpoint.

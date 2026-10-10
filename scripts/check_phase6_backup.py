"""Local-only PostgreSQL dump and isolated restore drill."""

import json
import os
import platform
import subprocess
import uuid
from pathlib import Path

REPORT = Path("artifacts/e2e/phase6-backup/report.json")
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "--env-file",
    ".env.phase4.local",
    "-f",
    "infra/compose.yaml",
    "-f",
    "infra/compose.phase4-e2e.yaml",
]


def postgres(command: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", "sh", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout.strip()


def fingerprint(database: str) -> str:
    # Return a digest only; neither row contents nor credentials enter evidence.
    query = (
        "SELECT md5(COALESCE(string_agg(id::text || ':' || status, ',' ORDER BY id), '')) "
        "FROM workflow_runs;"
    )
    return postgres(f'psql -U "$POSTGRES_USER" -d {database} -tA -c "{query}"')


def main() -> None:
    if os.getenv("PHASE6_ALLOW_BACKUP") != "1":
        raise RuntimeError("Set PHASE6_ALLOW_BACKUP=1 for the local Compose database")
    nonce = uuid.uuid4().hex[:12]
    temporary_database = f"automiq_restore_{nonce}"
    temporary_dump = f"/tmp/automiq_phase6_{nonce}.dump"
    scenarios: list[str] = []
    try:
        before = fingerprint('"$POSTGRES_DB"')
        postgres(
            f'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            f"--no-owner --no-privileges -Fc -f {temporary_dump}"
        )
        scenarios.append("application database dump completed")
        postgres(f'createdb -U "$POSTGRES_USER" {temporary_database}')
        postgres(
            f'pg_restore -U "$POSTGRES_USER" -d {temporary_database} '
            f"--no-owner --no-privileges {temporary_dump}"
        )
        scenarios.append("dump restored into isolated temporary database")
        after = fingerprint(temporary_database)
        if before != after:
            raise AssertionError("restored run fingerprint differed")
        scenarios.append("workflow run fingerprint matched after restore")
        status = "passed"
    except Exception as exc:
        status = "failed"
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.with_name("failure.json").write_text(
            json.dumps(
                {"status": status, "error_type": type(exc).__name__, "completed": scenarios},
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
    finally:
        # These names are generated locally and used only for this drill.
        postgres(f'dropdb -U "$POSTGRES_USER" --if-exists --force {temporary_database}')
        postgres(f"rm -f {temporary_dump}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "status": status,
                "scenarios": scenarios,
                "count": len(scenarios),
                "environment": {"python": platform.python_version(), "database": "postgres:16.6"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Phase 6 backup/restore drill passed: {len(scenarios)} scenarios")


if __name__ == "__main__":
    main()

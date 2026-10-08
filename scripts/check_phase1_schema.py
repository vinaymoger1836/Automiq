"""Exercise the migrated Phase 1 schema against PostgreSQL with rolled-back synthetic rows."""

import asyncio
import json
import uuid

from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


async def rejects(connection: AsyncConnection, sql: str, params: dict[str, object]) -> bool:
    try:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    except IntegrityError:
        return True
    return False


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    required_tables = {
        "users",
        "workspaces",
        "memberships",
        "workflows",
        "workflow_versions",
        "audit_logs",
    }
    required_indexes = {
        "ix_users_email_lower",
        "ix_memberships_user_workspace",
        "ix_workflows_workspace_updated",
        "ix_audit_logs_workspace_created",
    }
    suffix = uuid.uuid4().hex[:12]
    slug_a = f"schema-a-{suffix}"
    slug_b = f"schema-b-{suffix}"
    graph = json.dumps({"schema_version": "1.0", "nodes": [], "edges": []})

    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0001_phase1_core":
                raise RuntimeError("Phase 1 schema revision is not installed")
            tables = set(
                (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await connection.execute(
                        text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
                    )
                ).scalars()
            )
            if not required_tables <= tables or not required_indexes <= indexes:
                raise RuntimeError("Phase 1 tables or indexes are missing")

            await connection.rollback()
            transaction = await connection.begin()
            try:
                user_a = (
                    await connection.execute(
                        text(
                            "INSERT INTO users (oidc_issuer, oidc_subject, email) "
                            "VALUES ('https://synthetic.invalid', :subject, 'a@synthetic.invalid') "
                            "RETURNING id"
                        ),
                        {"subject": f"subject-a-{suffix}"},
                    )
                ).scalar_one()
                user_b = (
                    await connection.execute(
                        text(
                            "INSERT INTO users (oidc_issuer, oidc_subject, email) "
                            "VALUES ('https://synthetic.invalid', :subject, 'b@synthetic.invalid') "
                            "RETURNING id"
                        ),
                        {"subject": f"subject-b-{suffix}"},
                    )
                ).scalar_one()
                identity_unique = await rejects(
                    connection,
                    "INSERT INTO users (oidc_issuer, oidc_subject, email) "
                    "VALUES ('https://synthetic.invalid', :subject, 'duplicate@synthetic.invalid')",
                    {"subject": f"subject-a-{suffix}"},
                )

                workspace_a = (
                    await connection.execute(
                        text(
                            "INSERT INTO workspaces (slug, name) "
                            "VALUES (:slug, 'Synthetic A') RETURNING id"
                        ),
                        {"slug": slug_a},
                    )
                ).scalar_one()
                workspace_b = (
                    await connection.execute(
                        text(
                            "INSERT INTO workspaces (slug, name) "
                            "VALUES (:slug, 'Synthetic B') RETURNING id"
                        ),
                        {"slug": slug_b},
                    )
                ).scalar_one()
                slug_validated = await rejects(
                    connection,
                    "INSERT INTO workspaces (slug, name) VALUES ('INVALID SLUG', 'Synthetic')",
                    {},
                )
                await connection.execute(
                    text(
                        "INSERT INTO memberships (workspace_id, user_id, role) "
                        "VALUES (:workspace_id, :user_id, 'owner')"
                    ),
                    {"workspace_id": workspace_a, "user_id": user_a},
                )
                role_validated = await rejects(
                    connection,
                    "INSERT INTO memberships (workspace_id, user_id, role) "
                    "VALUES (:workspace_id, :user_id, 'admin')",
                    {"workspace_id": workspace_b, "user_id": user_b},
                )

                workflow_a = (
                    await connection.execute(
                        text(
                            "INSERT INTO workflows (workspace_id, name, draft_graph) "
                            "VALUES (:workspace_id, 'Synthetic A', CAST(:graph AS jsonb)) "
                            "RETURNING id"
                        ),
                        {"workspace_id": workspace_a, "graph": graph},
                    )
                ).scalar_one()
                workflow_b = (
                    await connection.execute(
                        text(
                            "INSERT INTO workflows (workspace_id, name, draft_graph) "
                            "VALUES (:workspace_id, 'Synthetic B', CAST(:graph AS jsonb)) "
                            "RETURNING id"
                        ),
                        {"workspace_id": workspace_b, "graph": graph},
                    )
                ).scalar_one()
                version_a = (
                    await connection.execute(
                        text(
                            "INSERT INTO workflow_versions "
                            "(workflow_id, version, graph_json, schema_version, checksum) "
                            "VALUES (:workflow_id, 1, CAST(:graph AS jsonb), '1.0', :checksum) "
                            "RETURNING id"
                        ),
                        {"workflow_id": workflow_a, "graph": graph, "checksum": "0" * 64},
                    )
                ).scalar_one()
                version_unique = await rejects(
                    connection,
                    "INSERT INTO workflow_versions "
                    "(workflow_id, version, graph_json, schema_version, checksum) "
                    "VALUES (:workflow_id, 1, CAST(:graph AS jsonb), '1.0', :checksum)",
                    {"workflow_id": workflow_a, "graph": graph, "checksum": "0" * 64},
                )
                foreign_pointer_rejected = await rejects(
                    connection,
                    "UPDATE workflows SET published_version_id = :version_id "
                    "WHERE id = :workflow_id",
                    {"version_id": version_a, "workflow_id": workflow_b},
                )
                await connection.execute(
                    text(
                        "UPDATE workflows SET published_version_id = :version_id "
                        "WHERE id = :workflow_id"
                    ),
                    {"version_id": version_a, "workflow_id": workflow_a},
                )
                await connection.execute(
                    text(
                        "INSERT INTO audit_logs (workspace_id, actor_type, actor_id, action, "
                        "resource_type, resource_id, metadata) VALUES "
                        "(:workspace_id, 'user', :actor_id, 'schema.checked', 'workflow', "
                        ":resource_id, '{}'::jsonb)"
                    ),
                    {"workspace_id": workspace_a, "actor_id": user_a, "resource_id": workflow_a},
                )
                checks = {
                    "identity_unique": identity_unique,
                    "slug_validated": slug_validated,
                    "role_validated": role_validated,
                    "version_unique": version_unique,
                    "foreign_pointer_rejected": foreign_pointer_rejected,
                }
                if not all(checks.values()):
                    raise RuntimeError("A Phase 1 database constraint did not reject invalid data")
            finally:
                await transaction.rollback()

        async with engine.connect() as connection:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM workspaces WHERE slug IN (:slug_a, :slug_b)"),
                {"slug_a": slug_a, "slug_b": slug_b},
            )
            if remaining != 0:
                raise RuntimeError("Synthetic schema check rows were not rolled back")
        print(
            json.dumps(
                {
                    "revision": revision,
                    "tables_present": sorted(required_tables),
                    "indexes_present": sorted(required_indexes),
                    "checks": checks,
                    "synthetic_rows_rolled_back": True,
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

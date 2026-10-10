"""Bounded, resumable run retention. Defaults to read-only dry run."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import session_factory
from app.models import (
    ActionEffect,
    Approval,
    AuditLog,
    IssueContent,
    RunEvent,
    RunStartOutbox,
    StepRun,
    WebhookDelivery,
    WorkflowRun,
)
from sqlalchemy import delete, or_, select, update


async def cleanup(*, apply: bool, batch_size: int) -> dict[str, int | bool]:
    settings = get_settings()
    now = datetime.now(UTC)
    detail_cutoff = now - timedelta(days=settings.run_detail_retention_days)
    summary_cutoff = now - timedelta(days=settings.run_summary_retention_days)
    async with session_factory()() as db:
        # Lock only terminal runs; never scrub or delete an active execution.
        rows = await db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.status.in_(["succeeded", "failed"]),
                WorkflowRun.completed_at < detail_cutoff,
                or_(
                    WorkflowRun.completed_at < summary_cutoff,
                    WorkflowRun.input_json != {"retained": False},
                ),
            )
            .order_by(WorkflowRun.completed_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        runs = list(rows)
        detail_ids = [row.id for row in runs]
        summary_ids = [row.id for row in runs if row.completed_at < summary_cutoff]
        result: dict[str, int | bool] = {
            "applied": apply,
            "details": len(detail_ids),
            "summaries": len(summary_ids),
        }
        if apply:
            if detail_ids:
                await db.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id.in_(detail_ids))
                    .values(input_json={"retained": False})
                )
                await db.execute(
                    update(StepRun).where(StepRun.run_id.in_(detail_ids)).values(output_json=None)
                )
                await db.execute(
                    update(RunEvent)
                    .where(RunEvent.run_id.in_(detail_ids))
                    .values(payload_json=RunEvent.payload_json.op("-")("output"))
                )
                await db.execute(delete(IssueContent).where(IssueContent.run_id.in_(detail_ids)))
            if summary_ids:
                for model in (
                    WebhookDelivery,
                    ActionEffect,
                    Approval,
                    RunEvent,
                    StepRun,
                    RunStartOutbox,
                ):
                    column = (
                        WebhookDelivery.result_run_id if model is WebhookDelivery else model.run_id
                    )
                    await db.execute(delete(model).where(column.in_(summary_ids)))
                await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(summary_ids)))
            await db.execute(delete(AuditLog).where(AuditLog.created_at < summary_cutoff))
            await db.execute(
                delete(WebhookDelivery).where(
                    WebhookDelivery.received_at < summary_cutoff,
                    WebhookDelivery.result_run_id.is_(None),
                )
            )
            await db.commit()
        else:
            await db.rollback()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit retention changes")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5000:
        parser.error("batch size must be between 1 and 5000")
    print(json.dumps(asyncio.run(cleanup(apply=args.apply, batch_size=args.batch_size))))


if __name__ == "__main__":
    main()

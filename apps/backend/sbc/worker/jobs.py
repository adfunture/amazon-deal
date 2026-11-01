"""Protokollierung und Deduplizierung von Hintergrundjobs."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import select

from sbc.db import session_scope
from sbc.integrations.redis_client import get_redis
from sbc.models import BackgroundJob, JobStatus


def acquire_dedup(key: str, ttl_s: int = 3600) -> bool:
    """True, wenn kein identischer Job läuft/wartet (Redis SET NX, Fallback DB)."""
    r = get_redis()
    if r is not None:
        return bool(r.set(f"job:dedup:{key}", "1", nx=True, ex=ttl_s))
    with session_scope() as db:
        return db.scalar(select(BackgroundJob.id).where(
            BackgroundJob.dedup_key == key,
            BackgroundJob.status.in_([JobStatus.queued, JobStatus.running]))) is None


def release_dedup(key: str) -> None:
    r = get_redis()
    if r is not None:
        r.delete(f"job:dedup:{key}")


def start(kind: str, payload: dict, org_id=None, dedup_key: str | None = None, priority: int = 5) -> str:
    with session_scope() as db:
        job = BackgroundJob(kind=kind, status=JobStatus.running, payload=payload, org_id=org_id,
                            dedup_key=dedup_key, priority=priority, attempts=1,
                            started_at=datetime.now(UTC))
        db.add(job)
        db.flush()
        return str(job.id)


def finish(job_id: str, t0: float, result: dict | None = None, error: str | None = None,
           dead: bool = False) -> None:
    import uuid

    with session_scope() as db:
        job = db.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return
        job.status = JobStatus.dead if dead else (JobStatus.failed if error else JobStatus.succeeded)
        job.result = result or {}
        job.last_error = error[:4000] if error else None
        job.finished_at = datetime.now(UTC)
        job.duration_ms = int((time.monotonic() - t0) * 1000)

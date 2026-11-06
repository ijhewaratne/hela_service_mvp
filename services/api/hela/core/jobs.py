"""Durable background jobs on PostgreSQL (Q69): enqueue inside the business transaction, claim with
FOR UPDATE SKIP LOCKED and a lease, run the handler and mark done in one transaction, retry with backoff,
dead-letter after max attempts. Handlers must be idempotent: delivery is at-least-once."""
from __future__ import annotations

import json
import logging
import socket
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine

from .db import many, run, tx

log = logging.getLogger('hela.jobs')
Handler = Callable[[Connection, dict], None]
LEASE = timedelta(minutes=2)


def enqueue(con: Connection, kind: str, payload: dict, *, run_at: datetime | None = None,
            dedupe_key: str | None = None, max_attempts: int = 5) -> None:
    run(con, '''INSERT INTO jobs(kind, payload, run_at, dedupe_key, max_attempts)
                VALUES (:k, CAST(:p AS jsonb), coalesce(:at, now()), :d, :m) ON CONFLICT (dedupe_key) DO NOTHING''',
        k=kind, p=json.dumps(payload, default=str), at=run_at, d=dedupe_key, m=max_attempts)


def _claim(engine: Engine, worker: str, limit: int) -> list[dict]:
    with tx(engine) as con:
        return many(con, '''UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_by = :w,
                                   locked_until = now() + :lease, updated_at = now()
                            WHERE id IN (SELECT id FROM jobs
                                         WHERE run_at <= now() AND (status = 'queued'
                                               OR (status = 'running' AND locked_until < now()))
                                         ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT :n)
                            RETURNING id, kind, payload, attempts, max_attempts''', w=worker, lease=LEASE, n=limit)


def run_due(engine: Engine, handlers: dict[str, Handler], *, worker: str | None = None, limit: int = 50) -> int:
    """Process due jobs once; returns how many were attempted."""
    worker = worker or f'{socket.gethostname()}:{id(engine)}'
    jobs = _claim(engine, worker, limit)
    for job in jobs:
        handler = handlers.get(job['kind'])
        try:
            if handler is None:
                raise LookupError(f"no handler for {job['kind']}")
            with tx(engine) as con:
                handler(con, job['payload'])
                run(con, "UPDATE jobs SET status = 'done', locked_until = NULL, updated_at = now() WHERE id = :id",
                    id=job['id'])
        except Exception as exc:  # noqa: BLE001 - every failure is recorded and retried or dead-lettered
            dead = job['attempts'] >= job['max_attempts'] or handler is None
            log.warning('job %s (%s) failed attempt %s: %s', job['id'], job['kind'], job['attempts'],
                        type(exc).__name__)
            with tx(engine) as con:
                run(con, '''UPDATE jobs SET status = :s, last_error = :e, locked_until = NULL, updated_at = now(),
                            run_at = now() + make_interval(secs => least(300, 5 * power(2, attempts)))
                            WHERE id = :id''',
                    s='dead' if dead else 'queued', e=f'{type(exc).__name__}: {str(exc)[:300]}', id=job['id'])
    return len(jobs)

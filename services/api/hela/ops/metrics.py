"""Prometheus metrics for the alerts the spec asks for (§17, §11.4): high error rates, stuck jobs and outbox,
competing acceptances, approvals past expiry, failed backups, unusual staff access to evidence, low supply,
incident response SLA. Alert rules live in infra/monitoring/prometheus/alerts.yml.

Two kinds of metric:
* Request metrics (counts, latency, error codes) are counted in process. With several API processes, set
  PROMETHEUS_MULTIPROC_DIR so they are aggregated (prometheus_client multiprocess mode).
* State metrics are read from the database at scrape time, so every API process reports the same values and a
  worker restart loses nothing.

No metric carries personal data: labels are route templates, status classes, error codes, job kinds, categories
and severities. Staff access is reported as counts (the busiest admin's count), not per-admin series.
The endpoint is mounted only when HELA_METRICS_TOKEN is set, and requires it as a Bearer token.
"""
from __future__ import annotations

import logging
import os
import secrets
import time

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import Response
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.multiprocess import MultiProcessCollector
from sqlalchemy import Engine

from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services

log = logging.getLogger('hela.metrics')
access_log = logging.getLogger('hela.access')

HTTP_REQUESTS = Counter('hela_http_requests', 'HTTP requests handled', ['method', 'route', 'status_class'])
HTTP_LATENCY = Histogram('hela_http_request_duration_seconds', 'HTTP request latency', ['route'],
                         buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0))
# Error responses by code (hela_api_errors_total) are counted in core/errors.py, where every envelope is built.


def _gauge(name: str, doc: str, value) -> GaugeMetricFamily:
    g = GaugeMetricFamily(name, doc)
    g.add_metric([], float(value or 0))
    return g


def _labelled(name: str, doc: str, label: str, rows: list[dict], key: str, val: str = 'n', expect=()) -> GaugeMetricFamily:
    g = GaugeMetricFamily(name, doc, labels=[label])
    seen = {}
    for r in rows:
        seen[str(r[key])] = float(r[val] or 0)
    for k in expect:
        seen.setdefault(k, 0.0)          # report zeros so alerts and dashboards have a series to compare
    for k, v in sorted(seen.items()):
        g.add_metric([k], v)
    return g


class StateCollector:
    """Reads operational state from Postgres at scrape time (bounded by a 2-second statement timeout)."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def describe(self):
        return []          # unchecked collector: families are only known at collect time

    def collect(self):
        started = time.monotonic()
        try:
            families = self._query()
            up = 1
        except Exception:   # noqa: BLE001 - a failed scrape must still report that the database is unreachable
            log.exception('metrics state query failed')
            families, up = [], 0
        yield _gauge('hela_db_up', 'Whether the metrics state query succeeded', up)
        yield _gauge('hela_metrics_state_query_seconds', 'Time taken to read state metrics', time.monotonic() - started)
        yield from families

    def _query(self) -> list:
        with tx(self._engine) as con:
            run(con, "SET LOCAL statement_timeout = '2s'")
            q = lambda sql, **p: one(con, sql, **p)  # noqa: E731
            out = []
            out.append(_labelled('hela_jobs', 'Background jobs by status (excluding finished jobs)', 'status',
                                 many(con, "SELECT status, count(*) AS n FROM jobs WHERE status <> 'done' GROUP BY 1"),
                                 'status', expect=('queued', 'running', 'dead')))
            j = q('''SELECT extract(epoch FROM now() - min(run_at) FILTER (WHERE status = 'queued' AND run_at <= now()))
                              AS overdue,
                            extract(epoch FROM now() - min(updated_at) FILTER (WHERE status = 'running')) AS running
                     FROM jobs WHERE status IN ('queued', 'running')''')
            out.append(_gauge('hela_jobs_oldest_overdue_seconds', 'Age of the oldest job that is due but not started',
                              j['overdue'] or 0))
            out.append(_gauge('hela_jobs_oldest_running_seconds', 'Age of the oldest job still marked running',
                              j['running'] or 0))
            out.append(_labelled('hela_outbox', 'Notifications by delivery status (excluding sent)', 'status',
                                 many(con, "SELECT status, count(*) AS n FROM notifications WHERE status <> 'sent' GROUP BY 1"),
                                 'status', expect=('queued', 'dead')))
            o = q("SELECT extract(epoch FROM now() - min(created_at)) AS age FROM notifications WHERE status = 'queued'")
            out.append(_gauge('hela_outbox_oldest_queued_seconds', 'Age of the oldest undelivered notification', o['age'] or 0))
            a = q('''SELECT count(*) AS n, extract(epoch FROM now() - min(occurred_at)) AS age FROM audit_logs l
                     WHERE NOT EXISTS (SELECT 1 FROM audit_chain c WHERE c.audit_id = l.id)''')
            out.append(_gauge('hela_audit_unsealed_rows', 'Audit rows not yet sealed into the hash chain', a['n']))
            out.append(_gauge('hela_audit_oldest_unsealed_seconds', 'Age of the oldest unsealed audit row', a['age'] or 0))
            p = q('''SELECT count(*) FILTER (WHERE valid_until < now()) AS overdue,
                            count(*) FILTER (WHERE valid_until BETWEEN now() AND now() + interval '7 days') AS soon
                     FROM provider_category_approvals WHERE status = 'approved' ''')
            out.append(_gauge('hela_approvals_past_expiry', 'Category approvals still active after their expiry date',
                              p['overdue']))
            out.append(_gauge('hela_approvals_expiring_7d', 'Category approvals expiring within 7 days', p['soon']))
            out.append(_labelled('hela_requests_open', 'Open service requests by status', 'status',
                                 many(con, '''SELECT status, count(*) AS n FROM service_requests
                                              WHERE status IN ('requested', 'awaiting_beneficiary', 'offering') GROUP BY 1'''),
                                 'status', expect=('requested', 'awaiting_beneficiary', 'offering')))
            r = q('''SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS created,
                            count(*) FILTER (WHERE status = 'unfulfilled'
                                              AND updated_at > now() - interval '1 hour') AS unfulfilled
                     FROM service_requests WHERE updated_at > now() - interval '1 hour' ''')
            out.append(_gauge('hela_requests_created_1h', 'Service requests created in the last hour', r['created']))
            out.append(_gauge('hela_requests_unfulfilled_1h', 'Requests that ran out of eligible providers in the last hour',
                              r['unfulfilled']))
            out.append(_labelled('hela_incidents_open', 'Open incidents by severity', 'severity',
                                 many(con, '''SELECT severity, count(*) AS n FROM incidents
                                              WHERE status NOT IN ('resolved', 'closed') GROUP BY 1'''), 'severity'))
            i = q('''SELECT count(*) AS n FROM incidents WHERE first_response_at IS NULL
                     AND status NOT IN ('resolved', 'closed') AND first_response_due < now()''')
            out.append(_gauge('hela_incidents_first_response_overdue', 'Incidents past their first-response target', i['n']))
            out.append(_gauge('hela_settlements_disputed', 'Offline payment acknowledgements in dispute',
                              q("SELECT count(*) AS n FROM settlements WHERE status = 'DISPUTED'")['n']))
            out.append(_gauge('hela_privacy_requests_overdue', 'Open privacy requests past their (unapproved) target',
                              q('''SELECT count(*) AS n FROM privacy_requests
                                   WHERE status NOT IN ('fulfilled', 'rejected') AND due_at < now()''')['n']))
            s = q('''SELECT coalesce(max(n) FILTER (WHERE kind = 'evidence'), 0) AS evidence,
                            coalesce(max(n), 0) AS any_kind, coalesce(sum(n), 0) AS total
                     FROM (SELECT admin_id,
                                  CASE WHEN resource_type = 'provider_evidence' THEN 'evidence' ELSE 'other' END AS kind,
                                  count(*) AS n
                           FROM sensitive_access_logs WHERE created_at > now() - interval '1 hour' GROUP BY 1, 2) t''')
            out.append(_gauge('hela_evidence_access_1h_max_per_admin',
                              'Most evidence grants/downloads by any one admin in the last hour', s['evidence']))
            out.append(_gauge('hela_sensitive_access_1h_max_per_admin',
                              'Most sensitive-data reads of one kind by any one admin in the last hour', s['any_kind']))
            out.append(_gauge('hela_sensitive_access_1h', 'Sensitive-data reads by all admins in the last hour', s['total']))
            out.append(_gauge('hela_otp_lockouts_1h', 'Sign-in codes locked after too many attempts in the last hour',
                              q('''SELECT count(*) AS n FROM audit_logs WHERE action = 'auth.otp_locked'
                                   AND occurred_at > now() - interval '1 hour' ''')['n']))
            beats = many(con, '''SELECT name, extract(epoch FROM now() - last_success_at) AS age,
                                        (last_failure_at IS NOT NULL
                                         AND (last_success_at IS NULL OR last_failure_at > last_success_at))
                                          AS failing
                                 FROM ops_heartbeats''')
            out.append(_labelled('hela_heartbeat_age_seconds', 'Seconds since the last successful run', 'name',
                                 [b for b in beats if b['age'] is not None], 'name', 'age'))
            out.append(_labelled('hela_heartbeat_failing', '1 if the latest run failed', 'name',
                                 [{**b, 'failing': int(b['failing'])} for b in beats], 'name', 'failing'))
            return out


def record_heartbeat(con, name: str, ok: bool, detail: str | None = None) -> None:
    col = 'last_success_at' if ok else 'last_failure_at'
    run(con, f'''INSERT INTO ops_heartbeats(name, {col}, detail) VALUES (:n, clock_timestamp(), :d)
                 ON CONFLICT (name) DO UPDATE SET {col} = clock_timestamp(), detail = EXCLUDED.detail,
                                                  updated_at = clock_timestamp()''',
        n=name, d=(detail or '')[:200] or None)


def _registry(engine: Engine) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(StateCollector(engine))
    return registry


def install(app: FastAPI, svc: Services) -> None:
    @app.middleware('http')
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get('route')
        template = getattr(route, 'path', None) or 'unmatched'
        HTTP_REQUESTS.labels(method=request.method, route=template, status_class=f'{response.status_code // 100}xx').inc()
        elapsed = time.perf_counter() - started
        HTTP_LATENCY.labels(route=template).observe(elapsed)
        # The access log (replaces uvicorn's): route template, not the raw path; no client IP or query string.
        quiet = template in ('/health', '/internal/metrics')
        access_log.log(logging.DEBUG if quiet else logging.INFO, 'request', extra={
            'http_method': request.method, 'route': template, 'status': response.status_code,
            'duration_ms': round(elapsed * 1000, 1), 'request_correlation_id': response.headers.get('X-Correlation-ID')})
        return response

    token = svc.settings.metrics_token
    if not token:
        return
    state = _registry(svc.engine)
    router = APIRouter()

    @router.get('/internal/metrics', include_in_schema=False)
    def metrics(authorization: str | None = Header(None)):
        supplied = (authorization or '').removeprefix('Bearer ')
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise ApiError(401, 'UNAUTHENTICATED', 'auth.required')
        if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
            process = CollectorRegistry()
            MultiProcessCollector(process)
        else:
            process = REGISTRY
        body = generate_latest(process) + generate_latest(state)
        return Response(body, media_type='text/plain; version=0.0.4; charset=utf-8')

    app.include_router(router)


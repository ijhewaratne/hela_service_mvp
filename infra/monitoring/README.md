# Monitoring (spec §17, §11.4)

Not connected to any environment. These files define *what* is measured and alerted on; where Prometheus and
Alertmanager run, and who gets paged, is part of the hosting decision (DEC-08, Q67).

* `prometheus/prometheus.yml`: example scrape configuration.
* `prometheus/alerts.yml`: alert rules. **Thresholds are synthetic defaults pending operations sign-off.**
* `prometheus/alerts_test.yml`: promtool unit tests for the rules (run in CI):

  ```bash
  docker run --rm -v "$PWD/prometheus":/w:ro -w /w --entrypoint promtool prom/prometheus:v3.5.0 test rules alerts_test.yml
  ```

## The metrics endpoint

`GET /internal/metrics` on the API, mounted only when `HELA_METRICS_TOKEN` (24+ characters) is set, and requiring
it as a Bearer token. It is not under `/v1`, and it should not be reachable through the public load balancer.

* **Request metrics**, counted in each process: `hela_http_requests_total{method,route,status_class}`,
  `hela_http_request_duration_seconds`, `hela_api_errors_total{code}`. Routes are templates
  (`/v1/bookings/{booking_id}`), never raw paths. With several API processes, set `PROMETHEUS_MULTIPROC_DIR`.
* **State metrics**, read from Postgres at scrape time with a 2-second timeout: jobs, outbox, audit sealing,
  approvals past expiry, open and unfulfilled requests, incidents past their first-response target, disputes,
  overdue privacy requests, sign-in code lockouts, staff access to sensitive data, and heartbeats.

No metric carries personal data. Staff access is reported as the busiest administrator's count, not per-admin
series; the alert's runbook says to look up who in `sensitive_access_logs`.

## Spec alert → rule

| Spec §17 asks to alert on | Rule(s) |
|---|---|
| High-error state | `HelaHighErrorRate`, `HelaApiDown`, `HelaDatabaseUnreachable`, `HelaSlowRequests` |
| Stuck outbox | `HelaOutboxStuck`, `HelaOutboxDeadLetters`, `HelaJobsStuck`, `HelaWorkerHeartbeatMissing` |
| Multiple winner attempts | `HelaCompetingAcceptancesHigh` |
| Expired approvals | `HelaApprovalsPastExpiry` |
| Failed backup | `HelaBackupFailed`, `HelaBackupStale` (from `scripts/backup_restore_check.sh`) |
| Unusual admin evidence access | `HelaUnusualEvidenceAccess`, `HelaUnusualSensitiveAccess` |
| §11.4 candidate supply too low | `HelaLowSupply` |
| Incident case SLA | `HelaIncidentResponseOverdue` |
| Also added | `HelaAuditSealingLag`, `HelaOtpLockoutSpike`, `HelaPrivacyRequestsOverdue`, `HelaPaymentDisputesHigh`, `HelaJobRunningTooLong`, `HelaDeadJobs`, `HelaAuditExportFailed`, `HelaAuditExportStale` (write-once audit export, [runbook](../../docs/security/AUDIT_EXPORT_RUNBOOK.md)) |

## Heartbeats

`ops_heartbeats` holds the last success and failure of periodic operations. The worker records `worker` every
15 seconds. The backup drill records `backup` when it finishes, or a failure if it exits early. `export-audit` records
`audit_export`. Other scheduled
checks can do the same:

```bash
python -m hela.cli record-heartbeat --name backup --status ok --detail "nightly"
```

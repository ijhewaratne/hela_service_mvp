# Workers

The R1 worker lives in the API package so it shares the domain code and transactions:

```bash
cd services/api && HELA_ENV=dev .venv/bin/python -m hela.worker
```

It claims due rows from the `jobs` table with `FOR UPDATE SKIP LOCKED` and a 2-minute lease, so several
workers can run at once. Each job's handler runs in the same transaction that marks it done. Failures are
retried with exponential backoff (5 s to 5 min). After `max_attempts` a job is dead-lettered, and dead jobs
appear in `GET /v1/admin/operations/queue`.

Job kinds: `dispatch.request`, `offer.expire`, `request.expire`, `consent.expire`, `completion.review`,
`notification.deliver`. All handlers are idempotent because delivery is at-least-once.

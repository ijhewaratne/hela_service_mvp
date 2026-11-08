# Hela Service

> **Synthetic local development only.** This code follows `Hela_Service_Master_System_Spec_v2.0` and the
> working answers in `Hela_Service_Decision_Register.csv`, which are **not yet owner-approved**. Nothing here is
> approved for real users, real personal data, payments or public deployment. Items marked `UNAPPROVED`
> or `PENDING` are placeholders for decisions in spec §23 (DEC-01 to DEC-12).

## Layout

| Path | What it is |
|---|---|
| `services/api/` | R1 FastAPI modular monolith (Python 3.12, PostgreSQL/PostGIS, Redis) |
| `services/api/migrations/` | Alembic migrations (run as `hela_owner`; the API runs as least-privilege `hela_app`) |
| `services/workers/` | How to run the background worker (`python -m hela.worker`) |
| `infra/docker/` | Docker Compose dev stack (PostGIS, Redis, MinIO, ClamAV), bound to 127.0.0.1 |
| `infra/monitoring/` | Prometheus scrape config, alert rules for spec §17 and their promtool tests |
| `infra/deploy/` | Cloud-neutral deployment skeleton (Dockerfiles, Kustomize). Not connected to any provider (Q67) |
| `apps/` | Flutter customer and provider apps, shared `hela_core` package, React admin console |
| `legacy/v0_2/` | The original SQLite prototype, frozen and still tested. Never run its demo auth in the new backend. |
| `docs/` | Spec questions, PRD, earlier design notes |
| `scripts/env_check.sh` | Checks Python 3.12, Docker and ports |

## Milestone status

| Milestone | Status |
|---|---|
| M01 Data foundation: Postgres/PostGIS, migrations, identity, authorization, audit | Done |
| M02 Provider trust: categories, onboarding, evidence storage, approvals, expiry | Done |
| M03 Booking engine: addresses, quotes, requests, matching, offers, bookings, settlement, incidents | Done |
| R1 gate hardening: multi-process/crash tests, load test, backup drill, ASVS self-assessment | **Done**: see [`docs/r1_gate/R1_GATE_REPORT.md`](docs/r1_gate/R1_GATE_REPORT.md) |
| R2: Flutter customer/provider apps, React admin web | Done: see [`apps/README.md`](apps/README.md). On-device testing is still open |
| Operations hardening: key rotation, metrics and alerts, deployment skeleton, accessibility checks, write-once audit export, log shipping, key service | Done: see below |

## Run locally (macOS)

```bash
scripts/env_check.sh
docker compose -f infra/docker/compose.yml up -d --wait
cd services/api
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e '.[test,dev]'
.venv/bin/alembic upgrade head
HELA_MIGRATION_DATABASE_URL=postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela_test .venv/bin/alembic upgrade head
.venv/bin/python -m hela.cli init-storage
HELA_S3_BUCKET=hela-evidence-test .venv/bin/python -m hela.cli init-storage
.venv/bin/python -m hela.cli seed-synthetic      # connects as the owner role: category policies are privileged
.venv/bin/python -m hela.cli create-admin --username ops.alice --role super_admin --reason "Local dev bootstrap"
HELA_ENV=dev HELA_DEV_INBOX_TOKEN=pick-a-local-token .venv/bin/uvicorn hela.asgi:app --host 127.0.0.1 --port 8000
HELA_ENV=dev .venv/bin/python -m hela.worker      # second terminal: dispatch, expiry, notifications
```

`create-admin` prints a TOTP secret and `otpauth://` URI **once**. Add it to an authenticator app.

API docs: http://127.0.0.1:8000/docs (dev/test only).

**Development OTP codes** go to a sandbox inbox, never to logs. Read the latest one with:
`POST /v1/dev/sms-inbox/latest` with body `{"phone": "+9477..."}` and header `X-Dev-Inbox-Token`.
This route exists only when `HELA_ENV=dev` and a token is set. Settings refuse it in staging and production.

**ClamAV** takes several minutes on first start (signature download, amd64 emulation on Apple Silicon).
Uploads made while it is unavailable are stored encrypted as `pending` and cannot be reviewed or opened until
`python -m hela.cli rescan-evidence` marks them clean. Run `python -m hela.cli expire-verifications` daily.

**Tests** (needs the Compose stack running): `cd services/api && .venv/bin/python -m pytest`
(167 tests, including real OS-process races, a SIGKILL crash test and real ClamAV. Add `-m "not multiprocess"` for a faster run.)

**App end-to-end tests** (the real Flutter apps against the real local API and worker, no fakes; synthetic data):
with the API started with a sandbox inbox token as above and the worker running,
`HELA_DEV_INBOX_TOKEN=<the same token> scripts/app_e2e.sh` runs both apps as macOS desktop builds, so no
emulator or phone is needed. The customer test signs up, accepts the draft notices, books cleaning and sees the
check-in code the server issued after a real provider accepts. The provider test accepts a real offer (the address
appears only after acceptance), goes on the way, arrives, checks in with the customer's code and finishes the job.
The same Dart code runs on Android. TalkBack, the Android Keystore, the camera and push notifications still
need a device.

**Load test and restore drill:** see `services/api/tests/load/` and `scripts/backup_restore_check.sh`. Results are in the R1 gate report.

**Legacy prototype tests:** `cd legacy/v0_2 && HELA_DEMO_MODE=1 python -m pytest`

## M01: what's implemented

- **Customer/provider sign-in (FR-ID-01/03/07):** phone OTP with hashed, single-use, 5-minute codes bound to the phone. 5 attempts per code, 3 sends per phone per 15 minutes, and a per-IP limit in Redis. If Redis is down, the rate limiter refuses requests (fails closed). The same response is returned whether or not the number has an account.
- **Sessions (Q9):** 10-minute JWTs, checked for issuer and audience, plus a server-side session check on every request. Refresh tokens are opaque, stored hashed and rotated on each use. Replaying an already-used refresh token revokes the whole session. Users can log out, log out everywhere, and list or end sessions.
- **Admins (Q10–Q15):** a separate admin identity with an argon2id password, then TOTP. Used TOTP codes cannot be reused. Accounts lock after 5 failed passwords. No admin session can open without TOTP enrolment.
- **Permissions (Q11):** deny by default, with roles mapping to permissions and optional region scoping.
- **Step-up (Q14):** sensitive actions need a fresh TOTP check, valid for 5 minutes and only for the named action.
- **Maker/checker (Q15):** enabling or disabling a region needs one admin to propose and a different admin to approve. The database also blocks self-approval. Staging and production refuse to enable a region that isn't marked approved.
- **Data protection (Q65):** phone numbers are AES-256-GCM encrypted, with keyed hashes for lookups. Only the last 3 digits are ever returned. Audit records drop secret fields automatically.
- **Audit (Q64):** the database computes a hash chain over the audit log and blocks UPDATE, DELETE and TRUNCATE. `GET /v1/admin/audit/verify` and `python -m hela.cli verify-audit` detect tampering. `export-audit` copies sealed rows to write-once (Object Lock) storage, so even an owner-level rewrite is caught.
- **Errors (Q62):** every error uses the spec §13.1 envelope with a `message_key` and correlation ID. Submitted values are never echoed back.
- **Notices (FR-ID-05):** versioned notices per language. Each acceptance records the version, language and processing context.
- **Localisation (Q61):** `si`/`ta`/`en` preference, with English as the reviewed source. `si` and `ta` are marked untranslated.

## M02: what's implemented

- **Categories (§4):** the spec's taxonomy is seeded with the three Q21 pilot categories enabled. Each category has versioned policies: allowed and prohibited tasks, durations, radius and required evidence. The database refuses edits to a policy's content, so changes need a new version. Childcare and clinical care can't be enabled, and a database constraint backs that up. Gated categories need an APPROVED policy. Enabling or disabling any category uses the same two-admin approval as regions.
- **Applying (FR-ID-06, Q13):** a provider applies with legal name and date of birth, both encrypted. Applicants under the minimum age (18, still a proposed rule) are refused, but the age the applicant enters only screens them out early. A reviewer still confirms age against ID and records the method.
- **Evidence uploads (Q28, Q38, SEC-07):** PDF, JPEG or PNG only, detected from file content (not the stated type), under a size limit, and PDFs with active content are rejected. Every upload is scanned by ClamAV.
  - **Infected files** are quarantined: only the hash is kept.
  - **If the scanner is unavailable**, the file is kept but locked from review and download until a rescan says it's clean.
  - **Storage:** each file is encrypted with its own key before it reaches MinIO, so the storage only ever holds ciphertext.
- **Reviewer access (Q14, AT-014):** case summaries never show identity details. Viewing a name or date of birth, or downloading a document, needs a fresh authenticator check and a stated purpose, and is recorded in an append-only access log. A download link works once, only for the admin who requested it, for at most 60 seconds, and the file is checked against its stored hash on the way out.
- **Decisions (FR-VER-02/03/05):** there are separate state machines for the provider's application and for each category approval. Every decision needs a reason and is recorded in an append-only history, including the policy version and the evidence used.
  - **No overrides:** an approval can't be granted while any required evidence is missing or expired (§8.1).
  - **Validity:** an approval lasts no longer than the earliest expiry or re-review date of the evidence behind it.
  - **Probation:** stored and visible to admins only.
- **Eligibility (FR-VER-04, INV-03):** `provider_category_gaps()` lists a reason code for every failed check. It reads expiry dates directly, so an expired provider is never treated as eligible even if the expiry job hasn't run yet. It also checks against the current policy version, so a stricter new version applies to existing approvals immediately.
- **Suspension (INV-11, Q39):** reviewers and incident responders can suspend a provider immediately, with no step-up, so protective action isn't slowed down. Reinstating needs a reviewer plus step-up. A hook is in place so that M03 can revoke pending offers and flag active jobs when a provider is suspended.
- **Expiry and reminders (FR-VER-07):** the expiry job is safe to run repeatedly, and an admin list shows evidence and approvals expiring soon. Nothing is ever approved automatically.

## M03: what's implemented

The whole booking flow works through the API and the worker:
address → quote → accept terms → request (beneficiary consent if booking for someone else) → automatic matching → offer to one provider at a time → atomic acceptance → en route → arrived → PIN check-in → work done → customer confirms or disputes → both sides acknowledge the offline payment → moderated review.

**Requests and pricing**
- **Addresses (FR-LOC):** the customer has to confirm the map pin. The exact address and pin are encrypted. Matching only uses a point rounded to about 550 m. An address outside an enabled region is refused (AT-029).
- **Quotes (Q29–Q31):** amounts are integer cents with currency `LKR`. Each quote breaks the price into parts, records the policy and price-rule versions, and is valid for 15 minutes. It's tied to that customer, address and duration, can be used once, and the customer must accept the terms version they saw. Price rules are versioned and marked UNAPPROVED.
- **Requests (FR-REQ, Q24–Q27):** every request needs an `Idempotency-Key`. Repeating a request returns the same response, and reusing a key with a different body is refused with 409. The server checks the notice period, how far ahead the booking is, the 30-minute duration steps and the policy's minimum and maximum. Tasks must be from the allowed checklist, and prohibited tasks are refused. A request expires 60 minutes before its start time if nobody has accepted it.
- **Beneficiaries (FR-BEN, Q19, Q20):** when booking for someone else, the beneficiary must confirm by SMS code before any dispatch. If they decline or don't respond, the request is cancelled. Staff can record consent on the beneficiary's behalf with step-up and a reason. The booker only ever sees the last 3 digits of the beneficiary's number, and the check-in PIN goes to the beneficiary.

**Matching and offers**
- **Matching (§8, Q40–Q45):** the mandatory checks run first:
  - the M02 eligibility check,
  - service area (the smaller of the provider's radius and the policy's),
  - a weekly Asia/Colombo slot that covers the whole job,
  - no blackout,
  - no overlapping job (with a 30-minute travel buffer),
  - the provider isn't the customer.

  Only providers who pass are ranked, using the spec's proposed weights. New providers get a neutral score for their first 5 offers, and expired offers don't count against anyone. Every run is stored with the counts and reason codes behind it. If nobody qualifies, the request is honestly marked `unfulfilled`.
- **Offers (Q41, Q42):** one offer at a time, valid for 10 minutes, up to 5 rounds. A decline or an expiry moves on to the next provider. Before accepting, the provider sees only the area name, a distance band, times, tasks and price.
- **Acceptance (§12.3):** the request, offer and provider are locked in a fixed order, and every check is re-run. If the provider is no longer eligible, the offer is revoked and the request is redispatched; that is committed before the error is returned (AT-003, AT-010, AT-011).
  - The database itself guarantees one live assignment per booking and refuses overlapping jobs for a provider. Two concurrent races were run 15 times each and always produced exactly one winner (AT-004, AT-005).
  - Accepting again after a lost response returns the same booking (AT-020).

**Bookings**
- **State machine (§7.2, Q47–Q51):** each step is limited to the right actor and checked against the booking's version, and every step is recorded.
  - **PIN check-in:** locks after 5 wrong codes. Staff can make an exception with step-up and a reason.
  - **Cancellation:** customers can cancel before the provider arrives. Providers can only cancel for safety, which needs a reason and always opens an urgent incident. Admins can cancel with a reason.
  - **Unconfirmed completion:** after 48 hours an alert appears for a human; bookings are never completed automatically.
  - **Reassignment:** keeps the same booking ID. The old provider loses access straight away, and the new provider still has to accept an offer.
- **Who sees what (INV-07, INV-08):** the customer and the provider of record can see the booking; anyone else gets 404. The provider sees the exact address and task note only while the job is active. Admin views never include the address.

**Payments, incidents and reviews**
- **Settlement (FR-PAY, Q53–Q55):** the payment goes from unpaid, to reported by the customer, to confirmed by the provider. If they disagree, it's marked disputed and a finance admin resolves it with evidence. The database refuses to record that Hela collected funds. Commission is a placeholder 0% receivable. The finance CSV export contains no personal data.
- **Incidents (FR-SAF, Q50, Q56–Q58):** incidents can be filed with or without a booking. The description is encrypted, only the reporter can view their own case, and urgent cases are flagged at once. Responders work a queue ordered by urgency with response deadlines, and every case view is logged. Putting a booking on hold is always an explicit action by a person. Emergency guidance is shown as `PENDING LOCAL VALIDATION`.
- **Reviews (FR-REV-01, Q59):** only the customer can review, only after completion, and only once. Review text waits for moderation, and possible phone numbers or emails are flagged.

**Infrastructure**
- **Notifications (FR-NOT, Q60):** an outbox with retries and duplicate protection. Messages never contain addresses, names or notes; they go through the sandbox SMS adapter.
- **Jobs (Q69):** a durable job queue with leases, backoff and dead-lettering, plus an operations queue and alerts (A02).

## Operations hardening: what's implemented

- **Key rotation (Q65):** every encrypted value names its key (`v2` format); `rotate-field-keys` re-encrypts in
  batches while the service runs, including append-only history rows (migration 0008 allows only `*_enc` columns
  to change, only for the owner, only during a rotation). Access tokens are Ed25519 (EdDSA) with a `kid`, so the
  signing key rotates with a verify-only overlap. `key-status` shows what is left under old keys.
  Phone lookup keys rotate too, with a dual-lookup period and `rotate-lookup-key`.
  With `HELA_KEY_PROVIDER=transit` every key is wrapped by a key service (OpenBao or Vault transit) and only
  unwrapped in memory at start-up; `docker compose ... up -d openbao` runs one locally.
  See [`docs/security/KEY_ROTATION_RUNBOOK.md`](docs/security/KEY_ROTATION_RUNBOOK.md).
- **Write-once audit export (Q64):** `export-audit` copies the sealed chain hourly to an S3 Object Lock bucket in
  chained segments; `verify-audit-export --against-db` detects a rewritten database chain, edited exports and
  hidden deletions. See [`docs/security/AUDIT_EXPORT_RUNBOOK.md`](docs/security/AUDIT_EXPORT_RUNBOOK.md).
- **Metrics and alerts (§17):** `/internal/metrics` (only with `HELA_METRICS_TOKEN`) and 24 alert rules covering
  each alert the spec lists: error rates, stuck outbox and jobs, competing acceptances, expired approvals, failed
  backups, unusual evidence access, low supply and incident SLA. Worker and backup heartbeats (migration 0009).
  See [`infra/monitoring/README.md`](infra/monitoring/README.md).
- **Log shipping (Q64):** JSON logs on stdout with correlation ids, masked in the app and again in the Vector
  collector; database errors keep only their SQLSTATE. See [`infra/deploy/logging/README.md`](infra/deploy/logging/README.md).
- **Deployment skeleton (Q67):** non-root images for API/worker/migrations and the admin console, and Kustomize
  manifests with default-deny networking and Pod Security `restricted`. See [`infra/deploy/README.md`](infra/deploy/README.md).
- **Accessibility (§16):** automated checks for touch targets, labels, contrast and 200% text on a small phone
  (Flutter), and axe-core checks on the admin console pages. Screen-reader testing on a real device is still open.
  The app end-to-end run found that the job and booking screens did not scroll, so on a small phone or with
  large text the actions at the bottom were cut off. That is fixed, and a widget test at 360 dp and 1.5× text
  covers it.
- **Security scans:** ruff's bandit rules (`S`) on every lint run; Semgrep rule packs (Python, secrets,
  Dockerfile, Kubernetes, TypeScript) with no open findings; `pip-audit` and `pnpm audit` report no known
  vulnerabilities. These are internal checks, not an independent assessment.

### Known gaps (tracked for the R1 gate)

- No real SMS vendor (DEC-08). Outside dev, the app refuses to pretend an SMS was sent.
- Keys can be wrapped by a key service (`HELA_KEY_PROVIDER=transit`, OpenBao/Vault, tested locally) and plaintext keys are refused in production; which key service runs there is DEC-08. Field, lookup and signing keys rotate without downtime ([runbook](docs/security/KEY_ROTATION_RUNBOOK.md)).
- Admin TOTP recovery is an operator procedure only, with no self-service yet.
- No real routing or ETA (Q44). Distances are straight-line, are labelled as approximate, and no arrival time is shown.
- There's no rescheduling or job extension (Q33, Q52), and no on-demand bookings (Q26). Each is either deferred or behind a disabled setting.
- Operations admins handle disputes and holds through the generic transitions endpoint. A dedicated dispute-adjudication screen comes with the R2 admin web.
- Infected files found by a rescan have their encryption key destroyed. The leftover ciphertext stays in storage, unreadable, until a retention job exists (DEC-09).
- `minio/minio` is no longer published on Docker Hub. The stack uses Chainguard's build, pinned by digest.

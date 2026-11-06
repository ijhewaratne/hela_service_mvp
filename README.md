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
| `services/workers/` | Outbox/background workers (M03) |
| `infra/docker/` | Docker Compose dev stack (PostGIS, Redis, MinIO, ClamAV), bound to 127.0.0.1 |
| `legacy/v0_2/` | The original SQLite prototype, frozen and still tested. Never run its demo auth in the new backend. |
| `docs/` | Spec questions, PRD, earlier design notes |
| `scripts/env_check.sh` | Checks Python 3.12, Docker and ports |

## Milestone status

| Milestone | Status |
|---|---|
| M01 Data foundation: Postgres/PostGIS, migrations, identity, authorization, audit | Done |
| M02 Provider trust: categories, onboarding, evidence storage, approvals, expiry | **Done** |
| M03 Booking engine: addresses, quotes, requests, matching, offers, bookings, settlement, incidents | Next |
| R2: Flutter customer/provider apps, React admin | After the R1 gate |

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

**Legacy prototype tests:** `cd legacy/v0_2 && HELA_DEMO_MODE=1 python -m pytest`

## M01: what's implemented

- **Customer/provider sign-in (FR-ID-01/03/07):** phone OTP with hashed, single-use, 5-minute codes bound to the phone. 5 attempts per code, 3 sends per phone per 15 minutes, and a per-IP limit in Redis. If Redis is down, the rate limiter refuses requests (fails closed). The same response is returned whether or not the number has an account.
- **Sessions (Q9):** 10-minute JWTs, checked for issuer and audience, plus a server-side session check on every request. Refresh tokens are opaque, stored hashed and rotated on each use. Replaying an already-used refresh token revokes the whole session. Users can log out, log out everywhere, and list or end sessions.
- **Admins (Q10–Q15):** a separate admin identity with an argon2id password, then TOTP. Used TOTP codes cannot be reused. Accounts lock after 5 failed passwords. No admin session can open without TOTP enrolment.
- **Permissions (Q11):** deny by default, with roles mapping to permissions and optional region scoping.
- **Step-up (Q14):** sensitive actions need a fresh TOTP check, valid for 5 minutes and only for the named action.
- **Maker/checker (Q15):** enabling or disabling a region needs one admin to propose and a different admin to approve. The database also blocks self-approval. Staging and production refuse to enable a region that isn't marked approved.
- **Data protection (Q65):** phone numbers are AES-256-GCM encrypted, with keyed hashes for lookups. Only the last 3 digits are ever returned. Audit records drop secret fields automatically.
- **Audit (Q64):** the database computes a hash chain over the audit log and blocks UPDATE, DELETE and TRUNCATE. `GET /v1/admin/audit/verify` and `python -m hela.cli verify-audit` detect tampering.
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

### Known gaps (tracked for M03+)

- No real SMS vendor (DEC-08). Outside dev, the app refuses to pretend an SMS was sent.
- Keys come from environment variables (`EnvKeyProvider`). A managed KMS provider is needed before staging.
- Admin TOTP recovery is an operator procedure only, with no self-service yet.
- New policy versions are created by the operator CLI or SQL as the owner role. An admin API with two-admin approval for policy versions comes later.
- Provider service area and availability are owned by M03.
- Infected files found by a rescan have their encryption key destroyed. The leftover ciphertext stays in storage, unreadable, until a retention job exists (DEC-09).
- `minio/minio` is no longer published on Docker Hub. The stack uses Chainguard's build, pinned by digest.

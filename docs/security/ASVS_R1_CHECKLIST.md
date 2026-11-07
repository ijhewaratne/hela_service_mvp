# OWASP ASVS 5.0 — R1 backend self-assessment

**Scope:** `services/api` at the R1 gate (commit after M03 + hardening). Synthetic local environment only.
**Status:** engineering self-assessment, **not** an independent verification (spec §14.2: no certification claim
until an independent assessment is completed). Mapped at ASVS chapter level. Requirement-level IDs and the
target level (L1/L2) are for the independent assessor to confirm against the published standard.

Legend: ✅ implemented and tested · 🟡 partial / design ready · ❌ not done · ➖ not applicable to R1 backend

| ASVS 5.0 chapter | Status | Evidence (code → tests) | Gaps / owner action |
|---|---|---|---|
| **V1 Encoding & sanitization** | ✅ | All SQL is parameterised (`hela/core/db.py`). The only dynamic SQL uses fixed model keys or fixed table names (ruff `S608` reviewed). JSON-only API, so no HTML rendering. Evidence downloads are sent as `Content-Disposition: attachment` with `nosniff` and a sandboxing CSP. | Add a SAST pass (Semgrep/Bandit) to CI in addition to ruff `S` rules |
| **V2 Validation & business logic** | ✅ | Pydantic schemas on every input. Server-side state machines for applications, approvals, requests, bookings, settlements and incidents. Idempotency keys (`core/idempotency.py`). Version checks. Rate limits on OTP, admin login and uploads. → `test_booking.py` state/idempotency tests, `test_provider_trust.py` transitions | Done: every request body extends `core.schema.Input` (`extra='forbid'`), so unknown fields fail with 422 (`test_unknown_fields_are_rejected_not_ignored`) |
| **V3 Web frontend security** | ➖ | No browser frontend in R1 | Applies to the R2 React admin: CSP, CSRF strategy (Q73), cookie flags |
| **V4 API & web service** | ✅ | Spec §13.1 error envelope, no stack traces (`core/errors.py`). Docs disabled outside dev/test. Request body limits are enforced on streamed bytes. Security headers, and `no-store` on `/v1` (`core/http.py`). Correlation IDs. → `test_security_headers_and_no_store`, `test_oversized_bodies_rejected_even_without_content_length`, `test_error_envelope_and_correlation_id` | Cursor pagination exists only on audit. Other list endpoints are bounded but not paginated |
| **V5 File handling** | ✅ | Allowlist checked by file content (magic bytes), not the stated type. Size cap. PDFs with active content rejected. Fail-closed ClamAV scan. Per-object envelope encryption, no public URLs. One-time downloads valid for 60 s or less, bound to the admin, and hash-verified. → `test_upload_validation`, `test_infected_upload_is_quarantined_not_stored`, `test_scanner_outage_fails_closed`, `test_evidence_download_is_one_time_logged_and_bound_to_admin`, `test_real_clamd_when_available` | Evidence objects are never deleted yet (DEC-09). Image re-encoding to strip EXIF is not done |
| **V6 Authentication** | ✅ | OTP is hashed, single-use, bound to the phone, limited by attempts, sends per phone and IP, and anti-enumeration. Rate limiting fails closed. Admins use argon2id plus TOTP with replay protection, lockout and a timing-equalised unknown-user path. No password-only admin session. Step-up for sensitive actions. → `test_identity.py`, `test_admin_and_authz.py` | No real SMS vendor (DEC-08). Admin MFA recovery is an operator runbook only. WebAuthn deferred (Q10). OTP is an onboarding channel, not identity proof (§14.3) |
| **V7 Session management** | ✅ | Server-side sessions are checked on every request. Refresh rotation with reuse detection revokes the session family. Logout and logout-all. 10-minute access tokens. 30-minute admin idle timeout. → `test_refresh_rotation_and_reuse_detection`, `test_logout_*`, `test_admin_session_idle_timeout` | Absolute admin session lifetime is 12 h. Confirm this with operations |
| **V8 Authorization** | ✅ | Deny by default. RBAC via a permission table with region scoping. Object-level checks on every booking, request, settlement and incident read and write (404, no existence leak). Separation of duties (no self-grant, no self-approval, admin identities separate). → `test_deny_by_default`, `test_region_scoped_permission`, `test_object_level_access`, `test_other_customers_cannot_use_my_address_or_request` | The authorization matrix should be reviewed by the independent assessor |
| **V9 Self-contained tokens** | ✅ | HS256 JWT with issuer, audience, expiry and not-before. Algorithm is pinned. Separate audiences for app and admin. The server-side session check means a stolen token dies on revocation. → `test_forged_expired_and_wrong_audience_tokens_rejected` | Move to asymmetric signing (EdDSA/RS256) with key rotation when the secret lives in a KMS |
| **V10 OAuth/OIDC** | ➖ | Not used | — |
| **V11 Cryptography** | 🟡 | AES-256-GCM with context-bound associated data for personal fields. Keyed HMAC lookups. Per-object data keys wrapped by a key-provider abstraction. `secrets` for all codes and tokens. Dev secrets are refused in staging/production. → `test_identity_fields_are_encrypted`, `test_stored_object_is_ciphertext_and_png_accepted`, `test_production_refuses_dev_secrets_and_sandbox` | **Keys come from environment variables.** A managed KMS provider and a key-rotation procedure are required before staging (Q65) |
| **V12 Secure communication** | 🟡 | All services bind to 127.0.0.1. HSTS is emitted in staging/production. | TLS termination, certificates and internal TLS to Postgres, Redis and MinIO are deployment tasks (DEC-08, Q67) |
| **V13 Configuration** | 🟡 | Least-privilege DB role (no DDL; append-only tables have no UPDATE or DELETE grants). Secrets from the environment, `.env` gitignored. Images pinned (MinIO pinned by digest). CI has pip-audit, an SBOM and gitleaks. `cryptography` upgraded after an audit finding. | **CI has never run, because there's no remote yet.** Add a hash-pinned lock file. Disable the dev inbox at the infrastructure layer as well |
| **V14 Data protection** | ✅ | Data minimisation:<br>• exact address only for the live assignment while working<br>• masked phone numbers<br>• no personal data in notifications, logs or audit details<br>• redacted finance CSV<br><br>Sensitive reads are logged with a purpose. `no-store` caching. → `test_end_to_end_cleaning_booking` (SMS bodies), `test_audit_never_stores_secret_fields`, `test_settlement_disagreement_goes_to_finance` (CSV) | The retention schedule itself is still DEC-09. Built: privacy requests, scoped own-data export (requires a recent sign-in), legal holds and a report-only retention register (`test_privacy.py`) |
| **V15 Secure coding & architecture** | ✅ | Modular monolith with transaction boundaries. Database constraints back the critical invariants (exclusion constraint, unique live assignment, `platform_collected_funds = false`, immutable policies). Fail-closed eligibility. Multi-process race and crash tests. → `test_multiprocess.py` | Threat model review (§14.1) with the security lead |
| **V16 Logging & error handling** | ✅ | Audit log is append-only at the database level and sealed into a hash chain in commit order, with a verify endpoint and CLI. Per-job timing logs without payloads. No personal data in server or worker logs: checked by searching the logs after every live run. OTP lockouts are audited. → `test_audit_chain_is_append_only_and_verifiable`, `test_concurrent_audit_writers_seal_into_one_intact_chain` | Central log shipping and alerting (§17), and an off-site write-once (WORM) audit export (Q64) |
| **V17 WebRTC** | ➖ | Not used | — |

## Findings fixed during the R1 hardening pass

| # | Finding | How found | Fix |
|---|---|---|---|
| F-1 | `cryptography` 46.0.7 had 7 published advisories | pip-audit (M02) | Upgraded to `>=50` |
| F-2 | Audit hash chain failed verification under concurrent writers. The global audit lock also serialised every audited transaction, and could deadlock against row locks | Integrity check after the load test | Migration 0005: append-only log plus a separate chain sealed by a SECURITY DEFINER function in commit order. Regression test with 4 writer processes |
| F-3 | An SMS gateway failure during an OTP request returned a 500 | AT-019 test | Now returns a retryable 503 `SMS_UNAVAILABLE`, and the challenge is rolled back |
| F-4 | Acceptance-time ineligibility rolled back the offer revocation and the redispatch | Booking tests (M03) | Commit the side effects, then return 409 |
| F-5 | No request-size limit and no default `no-store` | Hardening review | `SecurityMiddleware` |
| F-6 | Admin sessions had no idle timeout (A01) | Hardening review | 30-minute idle limit, enforced on both access and refresh |
| F-7 | The dev SMS inbox took the phone number in a URL query, so it was written to access logs | Searching the M01 live-run log | Moved to the request body |

## Open items before an independent assessment

1. Managed KMS and key rotation; asymmetric JWT signing.
2. Run CI on a real remote. Add a hash-pinned lock file and a SAST tool.
4. TLS everywhere (deployment).
5. Retention and deletion jobs (DEC-09) and the privacy-request workflow (Q66).
6. Central logging and alerting, and an off-site write-once (WORM) audit export.
7. Book an independent penetration test (NFR-07); ASVS for the API, MASVS for the R2 apps.

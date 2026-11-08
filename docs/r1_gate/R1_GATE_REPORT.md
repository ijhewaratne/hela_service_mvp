# R1 gate report: engineering evidence

**Date:** 2026-11-06 · **Scope:** `services/api` (R1 backend), Alembic head `0005_audit_sealing`
**Baseline:** Spec v2.0 + `Hela_Service_Decision_Register.csv` (working answers, **not owner-approved**)

> **Verdict.** The engineering evidence for gate **G0** (spec §19.3) is complete. For gate **G1** the engineering
> evidence is in place, with the items in §7 still open. Passing a gate is a human decision (§19.3, §22). This
> report does not approve anything for real users, real data or deployment.

## 1. How to reproduce

```bash
docker compose -f infra/docker/compose.yml up -d --wait
cd services/api
.venv/bin/python -m pytest                          # 112 tests, includes multi-process and real ClamAV
.venv/bin/python -m pytest -m multiprocess          # just the OS-process race and crash tests
python tests/load/seed_load.py /tmp/tokens.json     # isolated hela_load database (30 providers, 100 customers)
# start the API with 4 workers and 2 `python -m hela.worker` processes against hela_load, then:
HELA_LOAD_TOKENS=/tmp/tokens.json HELA_LOAD_BOOKINGS_PER_MIN=50 \
  .venv/bin/locust -f tests/load/locustfile.py --headless -u 40 -r 10 -t 180s --host http://127.0.0.1:8100
../../scripts/backup_restore_check.sh hela_load
```

**Test environment.** One Apple Silicon laptop running everything: four API processes, two workers, Locust and
the database stack. **PostGIS and ClamAV run under amd64 emulation.** All numbers below come from this setup. They
are a floor for engineering confidence, not a capacity claim; native Postgres on server hardware will differ.

## 2. System invariants (spec §7.3)

| ID | Invariant | Enforced by | Evidence |
|---|---|---|---|
| INV-01 | At most one live accepted assignment per request | Unique partial index `one_live_assignment_per_booking`; request row lock at acceptance | `test_competing_acceptances_produce_exactly_one_booking`; `test_competing_providers_across_processes` (4 rounds, 3 OS processes each); 0 violations after load |
| INV-02 | No overlapping incompatible jobs per provider (database-enforced) | `EXCLUDE USING gist (provider_id WITH =, busy WITH &&) WHERE (active)`, with a 30-minute travel buffer | `test_provider_cannot_hold_overlapping_jobs` (including a direct SQL insert); `test_one_provider_overlapping_offers_across_processes`; 0 violations after 165 load bookings |
| INV-03 | An expired, revoked or suspended category approval is never eligible | `gaps_from_facts` reads dates directly (fail closed), then is re-run at acceptance | `test_expired_evidence_blocks_immediately_and_job_records_it`; `test_acceptance_revalidates_evidence_and_category`; `test_revoke_and_re_review` |
| INV-04 | Acceptance re-evaluates every hard gate | `accept_offer` → `hard_gates` (same code as matching) inside the locking transaction | `test_acceptance_revalidates_evidence_and_category`; `test_suspension_revokes_pending_offer_and_redispatches` |
| INV-05 | Same actor, key and payload gives the same result; a changed payload is a conflict | `idempotency_records`; `service_requests (customer_id, idempotency_key)` unique | `test_request_idempotency`; `test_accept_retry_after_lost_response_returns_same_booking` |
| INV-06 | Every transition increments the version and appends one event | `apply_transition` with `UPDATE ... WHERE version = :old`; append-only `booking_events` | `test_end_to_end_cleaning_booking` (versions 1 to 6, six events); `test_state_machine_rules` |
| INV-07 | No unauthorised reads of private records | Object-level `role_for`, returning 404 to avoid revealing that records exist; deny-by-default RBAC | `test_object_level_access`; `test_other_customers_cannot_use_my_address_or_request`; `test_deny_by_default` |
| INV-08 | No exact address before assignment allows it | Offers show area and distance band only. The address is decrypted only for the live assignment in active states | `test_end_to_end_cleaning_booking`; `test_reassignment_keeps_booking_and_removes_old_provider` |
| INV-09 | No disabled category can be booked through any interface | DB `CHECK never_enable_forbidden`; `_bookable` on quote and request; eligibility; maker/checker | `test_disabled_and_forbidden_categories_cannot_be_booked`; `test_forbidden_categories_cannot_be_enabled_even_directly` |
| INV-10 | No offline payment can be recorded as money held by Hela | `CHECK (platform_collected_funds = false)` | `test_settlement_disagreement_goes_to_finance` (the direct UPDATE is refused) |
| INV-11 | An unsafe provider can be removed from matching immediately | Suspension needs no step-up, revokes pending offers and flags active jobs | `test_suspension_is_immediate_and_reinstatement_needs_step_up`; `test_suspension_revokes_pending_offer_and_redispatches` |
| INV-12 | History is never silently rewritten | Triggers block UPDATE and DELETE on all event and history tables; the app role has no write grants. Audit sealed into `audit_chain` | `test_audit_chain_is_append_only_and_verifiable` (a privileged tamper is detected); `test_concurrent_audit_writers_seal_into_one_intact_chain`; load DB verified intact; an owner-level rewrite of the whole chain is caught by the write-once export (`test_rewritten_database_chain_no_longer_matches_the_export`) |

## 3. Acceptance scenarios (spec §19.2)

| Test | Result | Evidence / note |
|---|---|---|
| AT-001 nearest provider lacks approval | ✅ | `test_ineligible_providers_are_never_offered`, `test_unapproved_provider_is_never_eligible` |
| AT-002 approved for cleaning, not elder care | ✅ | `test_full_workflow_makes_provider_eligible`, `test_ineligible_providers_are_never_offered` |
| AT-003 credential expired between offer and acceptance | ✅ | `test_acceptance_revalidates_evidence_and_category` |
| AT-004 two concurrent accepts | ✅ | threads ×15 and OS processes ×4 rounds: always exactly one winner |
| AT-005 one provider, overlapping jobs | ✅ | in-process and 4 OS processes; DB exclusion constraint |
| AT-006 identical retry | ✅ | `test_request_idempotency` |
| AT-007 same key, changed payload → 409 | ✅ | `test_request_idempotency` |
| AT-008 no supply | ✅ | `test_no_supply_is_honest_and_admin_can_retry` |
| AT-009 booking for a parent without authorisation | ✅ | `test_beneficiary_must_consent_before_dispatch`, `test_beneficiary_decline_or_silence_cancels` |
| AT-010 suspended provider holds an offer | ✅ | `test_suspension_revokes_pending_offer_and_redispatches` |
| AT-011 category disabled while offer pending | ✅ | `test_acceptance_revalidates_evidence_and_category` |
| AT-012 provider uses a customer-only endpoint | 🟡 | Every account gets a customer persona (Q12), so a provider *can* book as themselves, but never receives their own booking (`SELF_BOOKING`) and gets 404 on other customers' records. Customer-only accounts get 403 on provider endpoints (`test_object_level_access`). Confirm this reading of AT-012 |
| AT-013 unassigned provider requests a home address | ✅ | `test_object_level_access`, `test_reassignment_keeps_booking_and_removes_old_provider` |
| AT-014 admin reviews sensitive evidence | ✅ | `test_evidence_download_is_one_time_logged_and_bound_to_admin`, `test_identity_view_requires_step_up_and_is_logged` |
| AT-015 customer disputes completion | ✅ | `test_completion_dispute_needs_human` |
| AT-016 customer reports cash payment | ✅ | `test_end_to_end_cleaning_booking`, `test_settlement_disagreement_goes_to_finance` |
| AT-017 payment acknowledgements conflict | ✅ | `test_settlement_disagreement_goes_to_finance` |
| AT-018 provider safety issue or no entry | ✅ | `test_provider_safety_exit_opens_urgent_incident` |
| AT-019 SMS gateway fails | ✅ | `test_sms_outage_is_retried_and_never_marked_sent`, `test_otp_request_during_sms_outage_is_a_clean_retryable_error` |
| AT-020 network lost during acceptance | ✅ | `test_accept_retry_after_lost_response_returns_same_booking`, `test_crashed_lock_holder_does_not_block_or_corrupt_acceptance` |
| AT-021 en-route telemetry after arrival | ➖ | No location tracking in R1 (Q46), so nothing is collected |
| AT-022 childcare disabled | ✅ | `test_disabled_and_forbidden_categories_cannot_be_booked` |
| AT-023 critical Sinhala/Tamil notice | ❌ | Needs human-reviewed translations and the R2 apps (Q61). Message keys exist; si/ta are marked untranslated |
| AT-024 invalid file or virus upload | ✅ | `test_upload_validation`, `test_infected_upload_is_quarantined_not_stored`, `test_real_clamd_when_available` |
| AT-025 repeated OTP guesses | ✅ | `test_wrong_code_counts_attempts_then_locks` (lockout plus audit record), `test_send_cap_per_phone` |
| AT-026 restore from backup | ✅ (local) | `scripts/backup_restore_check.sh`: dump 1 s, restore 3 s, counts and schema identical, chain intact. Production needs WAL/PITR for RPO ≤ 15 min |
| AT-027 admin alters policy | ✅ | Region and category activation, and new **policy and price versions**, all go through maker/checker with step-up and audit (`test_publish_policy_version_with_maker_checker`, `test_price_publish_changes_new_quotes_only`). Marking a policy APPROVED stays owner-only because it records an external sign-off |
| AT-028 expired session | ✅ | `test_forged_expired_and_wrong_audience_tokens_rejected`, `test_admin_session_idle_timeout` |
| AT-029 unsupported locality | ✅ | `test_unsupported_locality_is_refused` |
| AT-030 TRUST/CLG account queries commercial users | ➖ | No TRUST/CLG role or access path exists. Keep it that way until a lawful data-sharing agreement exists (§1.3) |

## 4. Non-functional results (spec §16): measured, synthetic, single laptop

Load: 30 providers, 100 customers, 4 API processes, 2 workers, 40 Locust users (10 customers, 30 providers polling).
"Matching decision" means request creation until the customer sees an offer was issued (or an honest `unfulfilled`).

| ID | Target (PROPOSED) | Steady, 5 bookings/min target, 150 s | Burst, 50 bookings/min (10×), 180 s | Result |
|---|---|---|---|---|
| NFR-01 | Zero invariant violations under races | 0 failures, 0 overlaps, 0 double assignments | same; 165 bookings accepted | ✅ |
| NFR-02 | Core API p95 < 500 ms | Aggregate p95 93 ms. All endpoints < 500 ms except `GET /v1/categories` p95 530 ms (20 samples, every one from the cold first iteration) | Aggregate p95 350 ms; every endpoint p95 ≤ 220 ms | ✅ warm / 🟡 cold start |
| NFR-03 | First offer decision p95 < 3 s | p50 650 ms, p95 740 ms | p50 420 ms, p95 710 ms, max 925 ms | ✅ |
| NFR-05 | 95% of notifications queued within 10 s | Enqueued in the business transaction (0 s). Delivery queue wait p95 406 ms in the burst | | ✅ |
| NFR-06 | RPO ≤ 15 min, RTO ≤ 4 h (design goal) | Local restore 4 s for the synthetic dataset | | 🟡 needs WAL/PITR in the target environment |
| NFR-08 | Never show an unconfirmed accepted state | `booking_id` is shown only once a booking row exists; idempotent retries | | ✅ |
| NFR-10 | Every privileged action linked to actor, event and policy version | Audit with correlation ID; approvals store `policy_id`; quotes store policy and price versions | | ✅ |
| NFR-11 | Load test ≥ 10× pilot peak | 50/min = 10× the Q71 target of 5/min | | ✅ |
| NFR-12 | Accessibility | Automated: touch targets, labels, contrast and 200% text on a 360 dp phone (Flutter guideline tests); axe-core on 8 admin pages; contrast computed for both admin themes | | 🟡 automated only; TalkBack testing on a device open |
| NFR-13 | Portability | Cloud-neutral images and Kustomize skeleton, schema-checked; not deployed (Q67) | | 🟡 |
| NFR-04, 07, 09 | Uptime, pen test, localisation | not measurable in R1 locally | | ❌ open |

**What the load test found and how it was fixed:**

| Before | Cause | Fix | After |
|---|---|---|---|
| Matching decision p95 4.1–7.5 s | 292 queries per dispatch (N+1 per candidate); workers claimed batches of 10; 1 s idle polling | The safety rules became pure functions over bulk-loaded facts, so matching and acceptance share one code path (292 → 11 queries). Postgres `LISTEN/NOTIFY` wake-up. Job priorities. Single-job claims | p95 0.71 s |
| Audit chain verify failed under concurrency | Id order ≠ lock order; the global lock was held until commit | Migration 0005: commit-order sealing into `audit_chain` | Intact after 680 rows across concurrent processes |

## 5. Concurrency and crash testing (§12.3 "independent workers and process crashes")

| Scenario | Result |
|---|---|
| 3 client processes race on 3 parallel offers, 3-process uvicorn, 4 rounds | `[200, 409, 409]` every round |
| 4 client processes race on overlapping offers for one provider | `[200, 409, 409, 409]` |
| 3 `hela.worker` processes deliver 150 notifications | 150 sent, every one exactly once (`attempts = 1`); ≥2 workers participated |
| Worker killed with SIGKILL mid-run (400 notifications) | Killed after 65 sends with 35 leased jobs stranded. A new worker recovered them: 400 sent exactly once |
| Process holding the request row lock killed with SIGKILL | Acceptance blocked while the lock was held, then completed with exactly one booking |
| 4 processes write 400 audit rows concurrently | Sealed chain intact |

**At-least-once caveat.** Delivery is exactly-once *within the database*. The sandbox SMS adapter writes inside the
job's transaction. A real SMS vendor is an external side effect, so a crash between the vendor call and the commit
can send a message twice. Before go-live, pick a vendor that accepts an idempotency key (pass the notification ID) and record this as a DEC-08 requirement.

## 6. Security

See [`docs/security/ASVS_R1_CHECKLIST.md`](../security/ASVS_R1_CHECKLIST.md): chapter-level self-assessment, seven
findings fixed during R1 (F-1 to F-7), and the open items. **Not an independent assessment.**

## 7. Open items before G1 sign-off (engineering)

1. **CI has never run on GitHub**, because there's no remote yet (Q7). The workflow exists, with PostGIS, MinIO, lint, tests, pip-audit, SBOM and gitleaks.
2. **KMS-backed keys.** Rotation is built: versioned field keys with online re-encryption, and Ed25519 (EdDSA) token signing with key ids ([runbook](../security/KEY_ROTATION_RUNBOOK.md)). Lookup keys rotate with a dual-lookup period. Keys can now be wrapped by a key service (OpenBao/Vault transit, tested locally) and plaintext keys are refused in production; which key service runs there is DEC-08 (V11).
3. ~~Policy-version administration API~~: done (migration 0006).
4. **Retention periods and deletion** still need DEC-09. The register, legal holds (released only by a second person), the privacy-request workflow and the scoped own-data export are now built (migration 0007). The register is report-only, so nothing is deleted automatically.
5. **WAL/PITR backups** in the target environment; the restore drill there (NFR-06).
6. **Where logs and audit exports are stored** (Q64). Structured, masked logs and a tested collector pipeline are built ([logging](../../infra/deploy/logging/README.md)). The write-once audit export is built and tested against local MinIO with Object Lock ([runbook](../security/AUDIT_EXPORT_RUNBOOK.md)); the real bucket and retention period wait for DEC-08 and DEC-09. Metrics and the §17 alert rules are built and unit-tested ([`infra/monitoring`](../../infra/monitoring/README.md)); where Prometheus and Alertmanager run, and who is paged, waits for DEC-08.
7. **Independent security assessment** (NFR-07, §19.1).
8. Confirm the readings of **AT-012** (shared customer persona) and **AT-030** (no access path exists).
9. Matching uses straight-line distance only (Q44); no ETA is shown anywhere.

## 8. Decisions that block G2 regardless of engineering (§23)

DEC-01 operating entity · DEC-02 locality and support hours (the synthetic region is refused in staging/production)
· DEC-03 categories · DEC-04 verification methods · DEC-05 beneficiary and check-in policy · DEC-06 prices,
commission, cancellation (all price rules UNAPPROVED, 0% commission) · DEC-07 liability and insurance · DEC-08 vendors
· DEC-09 retention · DEC-10 incident team and emergency guidance (shown as PENDING LOCAL VALIDATION) · DEC-11
SLA targets · DEC-12 brand.

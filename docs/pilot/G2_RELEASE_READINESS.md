# Hela Service — Pilot Release Gate Checklist (G2)
Date: 2026-11-09 · State: **BLOCKED** · Applies to the commercial Hela Service platform only.

This is a tracking checklist, not an authorization to handle real user data. The source of authority is [Master System Specification v2.0](../SPEC_V2_CLARIFICATION_QUESTIONS.md) and `docs/r1_gate/R1_GATE_REPORT.md`. Business and legal decisions in DEC-01 through DEC-12 remain unapproved; no local fixture, synthetic rate or code test is a substitute.

## Implemented engineering, still requiring review
- [x] PostgreSQL/PostGIS, identity, session lifecycle, deny-by-default authorization, independent provider verification records, eligibility checks, offers/atomic assignment, customer/provider booking state machines, offline settlement and incidents.
- [x] Encrypted sensitive evidence and data, category-level approvals, time-bound access, audit events and write-once export mechanisms in local development.
- [x] Flutter customer/provider applications and React admin operations portal, including server-truth booking statuses.
- [x] Offline/session recovery and CI scanner tooling from PR #1 integrated into this PR's branch.
- [x] UX blueprint and research traceability from PR #2 integrated as documentation.
- [x] Guided customer booking, catalog search, provider error/empty-offer differentiation, admin triage priority screen in this PR.
- [ ] Complete **final combined-branch** CI (full job matrix) and resolve all findings; preserve race tests and explicit failure handling.
- [ ] Human code review, API/UI contract tests and operational signoff for each changed path.
- [ ] Merge with branch protection and required tests; do not deploy by merging alone.

## Production engineering not yet complete
- [ ] DEC-08: Choose a Sri Lankan SMS vendor; implement production adapter (including delivery receipts, vendor idempotency keys, retries, anti-abuse limits and privacy agreement). Test failure and duplicate-delivery scenarios.
- [ ] DEC-08: Choose hosting, region, production database, Redis, S3-compatible evidence storage, KMS/OpenBao/Vault transit, and mapping tiles; configure network/data protections and secrets outside Git.
- [ ] Production Postgres WAL/PITR with encrypted offsite backup, restore drill in target region, RPO/RTO evidence, operational alerting and assigned pager.
- [ ] Production WORM audit bucket, seal verification schedule, approved retention and immutable history recovery.
- [ ] DEC-09: Signed retention schedule by data category; implement/test deletion/cryptographic disposal and legal hold lifecycle. Existing retention register is report-only.
- [ ] Service/customer geocoding and manual address fallback tested locally; no route-based ETA or live GPS in the pilot without a separate approved specification.
- [ ] Human-reviewed English, Sinhala and Tamil consent, safety, policy, booking and support messages; font/overflow screen tests across locales. Existing si/ta fallback is **not** localization completion.
- [ ] Physical Android phone, TalkBack, slow network, small screen, 200% text and app-install test. Independent security and accessibility audit; address high-severity findings.
- [ ] Admin MFA/password recovery with verified staff identity, dual control for privileged resets, audit trail, privileged access review, operational change controls and incident tabletop test.
- [ ] Lost-phone/SIM customer and provider account recovery SOP with identity proof, rate limits, compromised-session revocation, fraud checks and independent audit.
- [ ] Registration/consent timing review: the current backend creates a minimal customer identity at first successful OTP verification, before the required versioned notices are acknowledged. Approve the lawful basis or redesign deferred registration before real-user processing.
- [ ] Unified role selector, OTP and profile setup verified on physical Android with TalkBack, local-language review and low-bandwidth conditions; admin portal HTTPS URL configured only in trusted builds.
- [ ] Customer/provider communication and incident support that works during the actual pilot operating hours; validate service-level objectives after load tests.
- [ ] Independently confirm that booking acceptance races return a single success and conflicts under repeated external-worker load.

## Business, legal, safeguarding and market decisions (owner approval required)
- [ ] DEC-01: Registration/identity of commercial operator; governance distinct from TRUST CLG and donations.
- [ ] DEC-02: Supported Sri Lankan pilot geography, service radius, support hours and local service capacity.
- [ ] DEC-03: Exactly which services and allowed/prohibited tasks are legal and staffed for the pilot; no clinical/childcare release.
- [ ] DEC-04: Provider identity/reference/qualification verification SOPs, renewal intervals, document handling and appeal procedures; actual vetted providers.
- [ ] DEC-05: Third-party beneficiary SMS consent, check-in, incident/no-entry and family/overseas booker policies.
- [ ] DEC-06: Approved price version, taxes, zero/nonzero platform commission, cancellation/no-show terms, offline receipt and receivables rules.
- [ ] DEC-07: Liability insurance and occupational/legal classification; customer/provider terms and financial contracting.
- [ ] DEC-08: Vendor contracts, data-transfer locations, key-management responsibility and documented processor controls.
- [ ] DEC-09: Privacy impact assessment (DPIA screening), lawful basis, data retention and customer privacy-rights policies.
- [ ] DEC-10: Staffed incident response, escalation tree, protective suspension and locally validated emergency guidance in all languages.
- [ ] DEC-11: Approved service/support SLAs and real pilot capacity/budget.
- [ ] DEC-12: Brand, domain, trademark and launch messaging.

## Release boundary
**G0:** synthetic demo and invariant tests. **G1:** engineering secure foundation and race/load tests. **G2:** closed pilot only after all relevant approvals, reviewed translations, vetted providers, working vendors, staffed operations, independent security assessment, tested backups, and explicit human signoff. **G3:** expansion only after measured pilot evidence.

**Not first-pilot blockers by themselves:** live GPS/ETA, iOS, on-demand instant dispatch, online payments/escrow, recurring subscriptions, bidding, regulated child/clinical care, or TRUST CLG integration. These remain separate products/features requiring new specifications.

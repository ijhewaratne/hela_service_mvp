# Hela Service — Engineering Handover Notes (0.2.0)

**Date:** 2026-11-04. **Status:** Complete local proof-of-concept vertical slice;
not a field-ready platform and not deployed anywhere.

## Traceability to product specification

| Requirement | Implemented now | Gap / follow-up |
|---|---|---|
| Service catalog with disabled high-risk services | Yes | Region and category policy console, translations |
| Customer request with owned address, 2-hour job | Yes | Family beneficiary consent and delegated booking |
| Price indication in LKR | Synthetic formula | Finance-approved tariffs and explicit quote approval |
| Provider profile/category eligibility | Seeded synthetic flags | Real verification evidence, secure document workflow |
| Schedule, credential expiry, radius, no overlap | Yes, demo rules | Production-grade spatial index, holiday/recurrence logic |
| Deterministic explainable matching | Yes | Road-network ETA, fairness review, measured weights |
| Offer TTL, accept/decline, single booking | Yes | Notification delivery and scalable worker queue |
| Two near-simultaneous offer acceptances | Tested | PostgreSQL and multiserver integration/concurrency tests |
| Bookings state machine / version check | Yes | Reassignment, cancellation fees, SLA handling |
| Request + transition retry safety | Yes | Global idempotency and notification outbox |
| Offline payment acknowledgement | Yes | Dispute reconciliation and accounting signoff |
| Incident submission and basic audit | Yes | Staff queues, SLAs, contacts, export retention |
| Object ownership / role restrictions | Synthetic role gates | Real auth, MFA, object security policies, pentest |
| Admin dispatch and category review | Yes, synthetic | Review evidence, four-eyes approvals where needed |
| UI / localization | Not yet | Flutter customer/provider; React admin; Sinhala/Tamil/English |
| Infrastructure, backup, monitoring | Not yet | Postgres/PostGIS, CI/CD deployment, observability |

## Independent architecture and reverse-engineering boundary

PickMe's *public user-facing patterns* inspired the design. No private PickMe APIs,
source code, credentials, traffic interception, scraping, undocumented endpoints,
or third-party personal information were used.

The matching formula is an **engineering hypothesis**:

`score = .45 * distance_radius_ratio + .25 * slack_penalty +
         .20 * synthetic_recent_decline_rate + .10 * synthetic_workload_penalty`

All factors are normalized to [0,1]. Distance is haversine straight-line, **not a
travel ETA**, and the algorithm is not asserted to represent PickMe internals.
Actual Sri Lankan routes, walking accessibility, safety concerns and service
category-specific rules must be evaluated before pilot matching.

## Security caveats

`X-Demo-Actor` is **insecure impersonation** for tests. A user can claim any seed
role if the demo is exposed. The environment-variable startup gate and localhost
binding are guardrails, not security guarantees. No sensitive data belongs here.
The bundled provider profile records are fictitious. Do not treat placeholder
`approved` statuses as evidence of actual background checks.

The SQLite `BEGIN IMMEDIATE` transaction is a single-node demonstration only.
Production workers require PostgreSQL with mutually consistent row-lock order,
unique/partial indexes, non-overlapping assignment constraints, transaction retries,
serialization-failure handling, and a transactional outbox for reliable notifications.

## Next milestone M02 — Operations-ready technical foundation

1. Implement real identity/authentication, verifiable roles and device sessions;
   secure admin MFA and account recovery. No public access before this.
2. Replace SQLite with PostgreSQL/PostGIS migrations and add database-enforced
   overlapping booking protection. Repeat acceptance stress tests across workers.
3. Develop provider application, evidence storage, reviewer workflows, expiration
   revalidation and actual category policies.
4. Add location addresses, provider working hours and coverage polygons with privacy
   and beneficiary consent gates.
5. Build basic React admin operations console to inspect requests, providers, offers,
   complaints and immutable event history.
6. Build a Flutter customer prototype in Sinhala/Tamil/English with service categories,
   addresses, booking request and status.
7. Build Flutter provider prototype with approval status, availability, offers, and
   confirmed booking tasks. Never expose exact household address until authorized.
8. Add event-driven notifications, cancellation/reassignment, support queue and
   explicit safety incident escalation procedures.
9. Establish written legal/commercial signoff: entity, privacy, local registration,
   provider terms, consent, insurance, taxes and offline payment policy.
10. Pilot with a limited number of adults in one locality and non-medical services,
    after independent safeguards verification and external security review.

**Priority recommendation:** implement M02.2 and M02.3 before UX polishing.
Correctness, legitimacy and actual service-provider trust are stronger
marketplace differentiators than mapping animations.

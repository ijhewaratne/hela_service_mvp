# Hela Service Master System Spec v2.0: questions to answer before implementation

**Source:** `Hela_Service_Master_System_Spec_v2.0.docx` (HELA-SYS-SPEC-002, 6 Nov 2026)
**Compared against:** the v0.2 code in this repository (FastAPI + SQLite, 17 tests passing, 38 live checks passing)

## How to answer

- Each question has a **suggested default** (my recommendation). You can reply `Q12: default` or just `defaults for all except Q3, Q7, Q40`.
- Questions marked 🔴 **block the start of work**. Questions marked 🟡 can be answered while work is under way, and I will use the default until you answer. ⚪ questions only matter later (R2 or after).
- Questions tagged **[DEC-xx]** are items the spec itself says are legal or business decisions (§23). I will not decide these myself. Unless you say otherwise, I will build them as **configurable placeholders** and clearly mark them "not approved".

---

## A. Scope and delivery: answer these first 🔴

**Q1. What does "implement fully" cover?** The spec runs from R0 to R4 and includes two Flutter apps, a React admin console, PostgreSQL/PostGIS, SMS/push, object storage, CI/CD and operational SOPs. Which of these do you want built?
- (a) **R1 engineering foundation only** (§1.4, milestones M01–M03): backend with real auth, Postgres/PostGIS, verification workflow, matching, booking engine, and the admin/API surface.
- (b) R1 + R2: also the Flutter customer and provider apps, the React admin web, notifications and localisation.
- (c) Everything the spec marks MUST, whatever its release.
- *Default: (a), and then you decide about R2.*

**Q2. Order of work.** §21 recommends vertical slices, starting with cleaning request → eligibility → offer → atomic accept → check-in → completion → offline payment → audit. Should I follow milestones M01 → M02 → M03 in that order, or deliver the first vertical slice end to end on the new stack first?
- *Default: M01 (data and auth foundation) first, then the vertical slice.*

**Q3. Repository layout.** §21.1 proposes a monorepo (`apps/`, `services/api/hela/<domain>/`, `services/workers/`, `infra/`, `tests/{unit,integration,...}`). Should I restructure this repo into that layout or keep the current flat `hela/` package?
- *Default: restructure into `services/api/hela/<domain>/` now. Create `apps/` only when that work starts.*

**Q4. What happens to the v0.2 SQLite demo?** Delete it, keep it as `legacy/`, or keep it runnable as the R0 demo next to the new stack?
- *Default: keep it as R0 under `legacy/v0_2/` with its tests, frozen.*

**Q5. Local tooling.** Docker isn't confirmed on this Mac, and Postgres/PostGIS, Redis, MinIO and ClamAV all need it. Can I assume Docker Desktop (or Colima) is installed, or should I set it up?
- *Default: assume Docker is available and provide `docker compose up` for all dependencies.*

**Q6. Python version.** The spec doesn't pin one. This Mac has 3.9 (system), 3.11 and 3.12.
- *Default: 3.12, with `requires-python >=3.12`.*

**Q7. Git.** This folder is not a git repository, but `.github/workflows` exists. Should I run `git init`? Is there a GitHub remote I should push to?
- *Default: `git init` locally and don't push until you give me a remote.*

---

## B. Identity, roles and authorisation (§3, §5.1, §14.3)

**Q8. 🔴 OTP provider for development and staging.** No SMS vendor has been chosen yet [DEC-08]. Should dev/staging use a fake SMS adapter that logs OTPs to the console, or a fixed test code?
- *Default: a pluggable `SmsSender` interface. In dev it writes the OTP to the server log, and a fixed-code bypass is allowed only when `ENV=dev`.*

**Q9. 🔴 Session tokens.** JWT access token plus rotating opaque refresh token, or opaque server-side sessions only? Revoking all sessions (FR-ID-03, FR-ID-07) is easier with server-side sessions.
- *Default: short-lived (10 min) signed access token plus an opaque refresh token stored hashed in `sessions`, with rotation and reuse detection.*

**Q10. Admin MFA method (FR-ID-04, SEC-04).** TOTP authenticator app, WebAuthn/passkeys, or both? How do admins log in? The spec says not SMS alone.
- *Default: email-or-username + password (argon2) + TOTP, with WebAuthn later.*

**Q11. Scoped admin permissions (§3.2).** "Verification reviewer", "incident responder" and "finance reconciler" are scoped permissions, not roles. Should I build a permission table (role → permissions), and what scopes do you want? Are permissions also scoped by **region**?
- *Default: permissions `verification.review`, `dispatch.manage`, `incident.respond`, `finance.reconcile`, `audit.read`, `policy.manage` (super-admin only), each optionally limited to region IDs.*

**Q12. Multiple roles per person (§3.1).** Can one phone number be both customer and provider? If yes, what separation of duties applies? For example, can a provider book their own services, and can an admin also be a provider?
- *Default: customer + provider is allowed on one account, but a provider can never be offered their own request. Admin accounts are separate and can't hold customer or provider roles.*

**Q13. Provider adult/age check (FR-ID-06).** How is "verified adult" shown? A date of birth checked against NIC evidence by the reviewer, or something else? What is the minimum age (18?).
- *Default: provider enters DOB, the reviewer confirms it against the ID document during verification, minimum 18, configurable.*

**Q14. Step-up authentication (§14.3).** Which actions need re-authentication? The spec lists changing phone, viewing evidence, granting roles and editing safeguards. Is that the complete list, and how long should a step-up stay valid?
- *Default: that list plus approving category activation. Valid for 5 minutes.*

**Q15. Dual approval (SEC-04, SEC-14).** Should enabling a category/region and editing safeguards require **two different admins** (maker/checker) in R1, or only an audit log?
- *Default: maker/checker for category/region enablement and safeguard edits. Everything else is audit only.*

---

## C. Regions, addresses and beneficiaries (§5.2)

**Q16. 🔴 Pilot region [DEC-02].** The v0.2 fixtures use Kandy. What locality should the synthetic seed region cover, and do you have a boundary polygon (GeoJSON), or should I draw an approximate one?
- *Default: an approximate synthetic polygon around central Kandy, clearly labelled as synthetic.*

**Q17. Address capture.** FR-LOC-01 says "search/manual pin". Is there a geocoding or search provider, or is it manual pin plus free-text only for R1?
- *Default: manual pin plus free-text fields for R1, with no geocoder.*

**Q18. Coarse location before assignment (FR-LOC-04, P05).** What should the provider see in an offer before accepting: a named area (e.g. "Peradeniya"), a rounded distance band, or a grid cell?
- *Default: the region/sub-area name plus a distance band (<2 km, 2–5 km, 5–10 km).*

**Q19. Beneficiary authorisation flow (FR-BEN-01–03, §6.2, AT-009) [DEC-05].** How does a beneficiary acknowledge a booking: SMS link, SMS reply code, or a phone call by support? Is acknowledgement required **before dispatch** for every category, or only for some?
- *Default: an SMS one-time link or code. Acknowledgement is required before dispatch for all categories in R1. If the beneficiary declines or doesn't respond within a configurable window, dispatch pauses.*

**Q20. Beneficiary data stored.** Name and phone only? Does the overseas booker see the beneficiary's phone number after entering it?
- *Default: name, phone and relationship label only. After saving, the booker sees only the last 3 digits of the phone.*

---

## D. Catalogue, category policy and requests (§4, §5.3)

**Q21. 🔴 Pilot categories [DEC-03].** The spec lists four PILOT CANDIDATE categories: cleaning, laundry/kitchen, errands, general hourly assistance. Should all four be seeded as enabled in synthetic data? The current code uses `house_cleaning`, but the spec example uses `household_cleaning`. Which codes should I use?
- *Default: seed all four, with codes `household_cleaning`, `laundry_kitchen`, `errands`, `general_assistance`. GATED/DISABLED/OUT OF SCOPE categories are seeded as disabled.*

**Q22. Category policy contents (§4.2).** Do you have the actual allowed/prohibited task lists, min/max durations, radius and cancellation terms for each category, or should I use clearly labelled placeholders?
- *Default: placeholders marked `UNAPPROVED`.*

**Q23. Prohibited-task enforcement (FR-REQ-02).** Should banned tasks be checked against free-text `task_details` with keyword filters, or should customers pick from a structured checklist of allowed tasks (C05)?
- *Default: a structured checklist of allowed tasks plus an optional free-text note. Keyword screening of the note only flags it for review and never auto-rejects.*

**Q24. Request payload shape.** The spec example uses `category_code`, `start_at` + `duration_minutes` and a required `quote_id`. The current API uses `category`, `start_at` + `end_at` and no quote. Should I adopt the spec shape and break the v0.2 contract?
- *Default: adopt the spec shape under `/v1`. v0.2 is frozen in `legacy/`, so no aliases are needed.*

**Q25. Scheduling limits.** v0.2 uses: start at least 5 minutes ahead, at most 60 days ahead, duration up to 8 hours. Are these the right per-category defaults? Are there minimum durations or slot granularity (e.g. 30-minute steps)?
- *Default: start at least 2 hours ahead, at most 30 days ahead, 1–8 hours in 30-minute steps, all set per category.*

**Q26. On-demand mode (FR-REQ-08).** Is on-demand (ASAP) booking in R1, or scheduled only?
- *Default: scheduled only. On-demand sits behind a feature flag that is off.*

**Q27. Request expiry.** The request state list includes `expired`. When does a request expire: start time passed without acceptance, or a separate deadline?
- *Default: it expires when `start_at` minus a configurable lead time (e.g. 60 minutes) passes without an accepted assignment.*

**Q28. Task photos (FR-REQ-07).** In R1 or deferred? Is ClamAV acceptable for malware scanning?
- *Default: deferred to R2. When built, use ClamAV in a sidecar container.*

---

## E. Pricing and quotes (§9) [DEC-06]

**Q29. 🔴 Money units.** The spec example shows `amount_minor: 240000` for LKR 2400, which means cents. LKR prices are normally whole rupees. Store in cents (×100) or whole rupees?
- *Default: integer cents (×100), ISO currency `LKR`, as the spec example shows.*

**Q30. Price formula.** `base_fee + hours × hourly_rate + travel_fee`. Do you have values for base fee and travel fee, or should I use placeholders? Is travel fee flat, banded by distance, or zero for the pilot?
- *Default: base fee 0, travel fee 0, current hourly rates (cleaning 1200/hr, errands 1000/hr), all versioned and marked illustrative.*

**Q31. Quote lifecycle.** How long is a quote preview valid before it must be re-priced? Can an accepted quote be reused for several requests?
- *Default: valid for 15 minutes. One quote per request.*

**Q32. `quote_required` categories.** Who writes the quote: the provider after viewing the job, or an admin? Is this needed in R1, given that the four pilot categories look like hourly or fixed?
- *Default: not in R1. The data model supports it, but no category uses it.*

**Q33. Hourly overrun and extensions (§9.1, P08).** If a job runs longer than planned, how is the extension agreed and priced? The spec mentions "published rounding/extension consent" and "safe extensions".
- *Default: R1 has no extensions. The provider completes the job and both sides note any overrun in the incident/dispute flow. Extensions come in R2.*

**Q34. Tax, commission and cancellation fees.** The spec says cancellation fee is 0 until approved, and commission is a receivable. What commission rate should the placeholder receivable use: 0%, or a configurable illustrative value?
- *Default: commission 0%, configurable. Cancellation fee 0. No tax lines.*

---

## F. Provider verification (§5.4, §20.1) [DEC-04]

**Q35. Evidence types.** Which document or evidence types should exist for each pilot category? For example: NIC front/back, Grama Niladhari certificate, police clearance, two references, training certificate. Do you have the actual list and validity periods?
- *Default: placeholders `national_id`, `address_proof`, `reference` (×2), `training_record`, each with configurable validity (ID 5 years, others 1 year).*

**Q36. Police/background check badge (§5.4 claim rule).** The spec forbids claiming "police checked" unless it really happened. Should the system support a police-clearance evidence type at all in R1?
- *Default: yes as an evidence type, but no customer-facing badge in R1.*

**Q37. Probation (FR-VER-08).** What does probation mean in the system? A fixed number of jobs, a time period, extra supervision, or limits on categories or hours?
- *Default: a `probation_until` date or a minimum job count per category approval, with a visible flag in the admin console only. No matching effect in R1.*

**Q38. Evidence storage.** Is MinIO acceptable locally (S3-compatible), and which encryption approach: server-side encryption, or app-level envelope encryption with a local KMS key?
- *Default: MinIO with server-side encryption locally, presigned GET URLs that expire after 60 seconds, and every access logged in `sensitive_access_logs`.*

**Q39. Effect of a mid-job suspension (§6.3).** When a provider is suspended during an in-progress job, should the system automatically place that booking on `incident_hold`, or just alert an admin?
- *Default: revoke pending offers automatically. In-progress bookings get an admin alert, with no automatic state change.*

---

## G. Availability, matching and dispatch (§5.5, §8)

**Q40. 🔴 Automatic or admin-triggered dispatch.** v0.2 only sends offers when an admin triggers dispatch. Spec §6.1 step 5 suggests matching runs automatically after a request is created, while A04 gives admins a dispatch console. Which is it for R1?
- *Default: automatic dispatch by a worker after request creation (plus beneficiary acknowledgement where needed). Admins can also re-dispatch or reassign by hand.*

**Q41. 🔴 Sequential or parallel offers.** §8.4 says to *start with sequential offers*, but v0.2 sends 2 in parallel. Which for R1?
- *Default: sequential, one provider at a time, with configurable `parallel_offers` defaulting to 1.*

**Q42. Dispatch parameters (§8.5).** What values should `offer_ttl_seconds`, `max_dispatch_rounds`, `radius_km` and the retry interval have?
- *Default: TTL 600 s, max 5 rounds, radius from the provider's service area capped by the category policy, no back-off between rounds.*

**Q43. Availability model (P04).** Weekly recurring hours plus blackout dates, or explicit dated slots only? Should there be a travel buffer between consecutive jobs for the same provider?
- *Default: weekly recurring hours (Asia/Colombo) plus blackouts, and a configurable 30-minute buffer that the overlap exclusion constraint includes.*

**Q44. Routing/ETA provider (§8.2).** Self-hosted OSRM/Valhalla on OSM data, a hosted API, or straight-line distance only for R1?
- *Default: straight-line distance via PostGIS, always labelled "approximate straight-line", behind a `RoutingAdapter` interface. No ETA shown.*

**Q45. Ranking inputs (§8.3).** How should "recent_reliable_decline_penalty" and "allocation_fairness_penalty" be calculated? Over what time window should declines and assignments be counted? Should a provider's first N offers count as neutral so newcomers aren't penalised?
- *Default: a 30-day window. Decline rate = declined ÷ offered, ignoring offers that expired while the provider was offline. Fairness = provider's assignments ÷ cohort median. Providers with fewer than 5 offers get neutral (0.5) values.*

**Q46. En-route location tracking (FR-MAT-10, AT-021).** Is live provider location during `en_route` in R1?
- *Default: no. Deferred to R2, behind a flag.*

---

## H. Booking lifecycle (§5.6, §7)

**Q47. 🔴 Who can cancel, and when?** Spec §7.2 says "authorized cancellation" but doesn't name the actors. Can the customer cancel during `en_route` or `arrived`? Can the provider cancel at all, for example in an emergency or on a no-entry refusal (FR-BKG-07)?
- *Default: the customer can cancel in `accepted` and `en_route`. The provider can cancel only through a "safety / unable to attend" reason, which also creates an incident. An admin can cancel in any non-terminal state with a reason.*

**Q48. Check-in PIN (FR-BKG-02).** How long is the PIN, and who receives it: the booker, the beneficiary, or both? How is it delivered (shown in app, SMS to beneficiary)? How many wrong attempts before it locks? What is the "staff-assisted exception"?
- *Default: 4 digits, shown in the booker's app and sent by SMS to the beneficiary if there is one. Locks after 5 attempts. The exception is that an admin with `dispatch.manage` records a reason and moves the booking to `in_progress`.*

**Q49. Auto-completion.** If the customer never confirms `completed_pending_confirmation`, does the booking auto-complete after a timeout? The spec says "no automatic closure" for disputes but says nothing about plain silence.
- *Default: no auto-completion. After 48 hours, an item appears in the admin queue.*

**Q50. New states compared with v0.2.** Spec §7.2 adds `accepted/en_route/arrived → incident_hold`. Who can trigger `incident_hold`: admin only, or also automatically when a high or critical incident is filed?
- *Default: admin only. A high or critical incident creates an urgent admin queue item but doesn't change state automatically.*

**Q51. Reassignment (FR-MAT-07, §6.4).** Does a reassigned booking keep its booking ID with a new `booking_assignments` row, or is a new booking created? Which states can a booking be reassigned from?
- *Default: same booking ID with a new assignment row, the old row closed with a reason. Allowed from `accepted` and `en_route` only.*

**Q52. Rescheduling (FR-BKG-09).** In R1, or deferred?
- *Default: deferred. In R1 the customer cancels (no fee) and re-books.*

---

## I. Settlement and finance (§5.7, §9.2)

**Q53. Who can mark a payment `DISPUTED`?** The customer, the provider, either one, or only an admin after a conflict? What happens if the provider says "not paid" after the customer reported paying (AT-017)?
- *Default: the provider can reply "not received", which sets `DISPUTED` and opens an admin finance case. Only an admin with `finance.reconcile` can resolve it.*

**Q54. Payment evidence (FR-PAY-02).** What evidence can be attached: a photo of a bank-transfer receipt, a reference number, or text only?
- *Default: a reference number and an optional note in R1. File uploads come with the evidence-storage work.*

**Q55. Finance exports (FR-PAY-06).** What format, and which fields count as "safely redacted"?
- *Default: CSV of booking ID, dates, category, amounts, payment status and commission receivable, with no names, phones or addresses.*

---

## J. Incidents, reviews and notifications (§5.8, §6.5)

**Q56. Incidents without a booking.** FR-SAF-01 and `POST /v1/incidents` suggest incidents can be filed without a booking, such as harassment through the app or a general safety concern. Should `booking_id` be optional?
- *Default: optional, with a reporter-only view.*

**Q57. Incident SLAs (FR-SAF-02) [DEC-10].** What first-response SLA applies to each severity level?
- *Default placeholders: critical 15 min, high 1 h, medium 1 business day, low 3 business days, all marked unapproved.*

**Q58. Emergency guidance text (FR-SAF-03, §14.4).** Who provides the locally checked emergency numbers and wording (e.g. 119 police, 1990 Suwa Seriya ambulance)? Should I show placeholders until it's approved?
- *Default: placeholders marked "PENDING LOCAL VALIDATION". I won't treat any number as verified.*

**Q59. Reviews (FR-REV-01).** What rating scale, should both sides review each other, and how is moderation done: by hand, or an automatic PII/profanity filter plus manual?
- *Default: customer rates provider 1–5 with optional text. Text is held until an admin approves it, with an automatic phone/email detector flagging items. No provider→customer reviews in R1.*

**Q60. Notification channels in R1.** Push (FCM) needs the mobile apps. For R1 backend only, should the notification outbox send through fake adapters (log/console), or connect to a real SMS sandbox?
- *Default: fake adapters with full outbox, retry and delivery-attempt tracking. Real vendors after DEC-08.*

---

## K. Localisation and accessibility (§10.4)

**Q61. Translations.** Who will provide the reviewed Sinhala and Tamil strings? Should I create the message-key files with English text and mark si/ta as "untranslated"?
- *Default: yes, use message keys everywhere. Sinhala and Tamil fall back to English with an `UNTRANSLATED` marker. I will not machine-translate safety, consent or legal text.*

**Q62. API errors.** Should API errors return only `message_key` (the client translates), or also an English `message`?
- *Default: both, as the §13.1 example shows.*

---

## L. Data, privacy and audit (§12, §15)

**Q63. Retention periods [DEC-09].** Should I build the retention framework (per-record-type policy plus scheduled job) with every period set to "keep indefinitely, pending approval"?
- *Default: yes, the framework with no deletion turned on.*

**Q64. Tamper evidence for audit/events (§12.2).** Is a hash-chained append-only audit table enough for R1, with database permissions that block UPDATE/DELETE? Is a write-once external export needed?
- *Default: hash chain plus DB-level revoke of UPDATE/DELETE, plus a daily integrity-check job. External WORM export later.*

**Q65. PII encryption at rest.** Which fields need app-level encryption beyond disk encryption: phone, exact address, beneficiary contact, incident text?
- *Default: app-level encryption (AES-GCM, key from environment/KMS) for phone, exact address coordinates and text, beneficiary contact, and incident descriptions. Phone is also stored as a keyed hash for lookups.*

**Q66. Privacy requests (FR-ID-08, §15.2).** Build intake and tracking only (admin handles them by hand), or also automated export/deletion?
- *Default: intake and tracking plus automated JSON export of a user's own data. Deletion stays manual, with a legal-hold check.*

---

## M. Infrastructure and operations (§11, §16, §17)

**Q67. Hosting target [DEC-08].** Is any cloud or region decided? If not, should I deliver only `docker compose` for dev and staging, with no infrastructure-as-code for production?
- *Default: docker compose for dev/staging, plus a cloud-neutral IaC skeleton that isn't connected to any provider.*

**Q68. Redis.** The spec lists it for cache and rate limits. Is it acceptable in R1, or should rate limiting run in Postgres to reduce moving parts?
- *Default: include Redis for rate limiting and OTP throttling.*

**Q69. Worker technology.** A plain Python outbox poller (`SELECT ... FOR UPDATE SKIP LOCKED`), or a framework like Celery/RQ/Dramatiq?
- *Default: a plain poller on Postgres SKIP LOCKED, so no extra broker is needed.*

**Q70. CI.** Keep GitHub Actions and extend it with Postgres service containers, dependency scanning (pip-audit), secret scanning (gitleaks) and an SBOM (CycloneDX)?
- *Default: yes.*

**Q71. Load and performance targets (NFR-02/03/11).** Should I add a load-test harness (e.g. Locust or k6) now, given that pilot peak load isn't defined? What peak should I assume?
- *Default: add Locust and assume 5 requests/minute pilot peak, so 10× = 50/minute.*

---

## N. Apps (only if Q1 includes R2) ⚪

**Q72. Flutter targets.** Android only first (as the spec says)? What minimum Android version? Are low-end devices a constraint?
- *Default: Android 8+ (API 26), tested on a low-RAM emulator profile.*

**Q73. Admin web hosting and auth.** Should the React admin be a separate SPA calling the same API, or served by the API? Is it only reachable over a VPN or IP allowlist?
- *Default: a separate SPA with an IP allowlist in staging and production.*

**Q74. Design assets.** Do you have branding (name, logo, colours) [DEC-12] or wireframes for C01–C13, P01–P10 and A01–A10, or should I use a neutral unbranded design?
- *Default: neutral design with "Hela Service (working name)".*

---

## O. Inconsistencies I found in the spec

These need a ruling because the spec contradicts itself or the existing code:

| # | Where | Conflict | Suggested resolution |
|---|---|---|---|
| X1 | §13.2 settlement routes | Spec uses `/payment-report` and `/payment-ack`; v0.2 uses `/settlement/customer-reported` and `/settlement/provider-confirmed` | Use the spec names |
| X2 | §13.2 incidents | Spec uses `POST /v1/incidents`; v0.2 uses `POST /v1/bookings/{id}/incidents` | Use the spec route, with optional `booking_id` (see Q56) |
| X3 | §13.2 approvals | Spec uses `POST /v1/admin/provider-approvals/{id}/decision`; v0.2 uses `/admin/providers/{id}/category-approvals` | Use the spec route |
| X4 | §7.1 vs v0.2 | Spec approval states add `under_review`, `needs_more_info`, `expired`; v0.2 has `pending/approved/denied/revoked` | Use the spec states |
| X5 | §8.4 vs v0.2 | Sequential vs parallel offers | See Q41 |
| X6 | §6.1 vs A04 | Automatic vs admin dispatch | See Q40 |
| X7 | §13.1 vs v0.2 | Error format `{"error":{code,message_key,...}}` vs FastAPI `{"detail":...}` | Use the spec format everywhere, including validation errors |
| X8 | §7.2 | `in_progress → cancelled` isn't allowed, but provider safe-exit (FR-SAF-05) may need to stop a job mid-way | Route through `incident_hold` → `cancelled_after_review` |
| X9 | §4.1 | "General hourly assistance" overlaps the other pilot categories. Its allowed duties aren't defined | Need a task list, or drop it from the pilot (see Q22) |

---

## P. Things I will NOT build unless you say so

These are listed so nothing is assumed:
- Online payments, wallets, refunds, escrow (FR-PAY-07, R4)
- GATED, DISABLED or OUT-OF-SCOPE categories (elder care, childcare, trades, driving, tutoring, clinical)
- Any automated background check, AI safety judgement or automatic sanctions
- Live GPS tracking (unless Q46 says yes)
- Any integration with TRUST/CLG systems or data (§1.3, AT-030)
- Production deployment or real user data. All work stays synthetic until the G2 gate is signed off.
- Any wording that claims legal, PDPA, insurance or verification approval

# Hela Service — Experience Blueprint v1
Date: 2026-11-08 | STATUS: Proposed UX, not live code | Baseline: `main` at `9009614`

**Goal:** Design all three commercial Hela Service surfaces as an internally consistent, low-bandwidth, multilingual and verifiably safe marketplace. Preserve the already-implemented API, role controls, category eligibility, explicit beneficiary consent, booking state machine and offline-payment accounting. Future capabilities are labeled **DEFERRED**.

## Product principles and architecture
- "Service-specific, not taxi-with-new-labels": category -> scoped tasks -> location -> beneficiary -> schedule -> quote -> provider match -> job.
- One primary action, one helpful explanation and an honest status for each view.
- Make confidence evidence specific and auditable. Badges and price/cancellation details are server-driven, not implied by a design.
- Consumer and provider are distinct applications; administration is a separate permission-filtered web application.
- The customer may be booking for a separate consenting beneficiary; distinguish booker, payer, recipient and address owner.
- Only enrolled/eligible providers receive offers; an exact address is released only after accepted assignment. No pre-assignment chat that leaks addresses.
- Company app excludes CLG/social-program modules and fundraising.
- Distinguish **CURRENT API** from **REQUIRES NEW API** and **BUSINESS APPROVAL REQUIRED** before coding.

## Navigation / information architecture

### Customer
Bottom navigation (proposed): **Home** | **Bookings** | **Help** | **Account**. If 4-tab layout crowds 360dp Sinhala/Tamil, move Help into a persistent toolbar action and keep 3 tabs.
- Home: saved location/area, main "What help do you need?" prompt, supported categories, active booking strip, recent tasks for reference (never one-click duplicate without fresh quote), helpful support entry.
- Bookings: Upcoming/Active/Past, each with status, address label, time, price basis, urgent help.
- Help: searchable FAQs and human incident flow; safety access preserved across booking states.
- Account: language, addresses, family/beneficiaries, privacy data request, notifications, sign-out.

### Provider
Bottom navigation: **Offers** | **Jobs** | **Availability** | **Profile**. Availability can remain within Profile until API/UI readiness.
- Unverified state: "Complete your application" and evidence/approval gap cards; no misleading acceptance button.
- Verified state: relevant offers with expiration and non-sensitive area, today's upcoming work, service-category approval status, earnings/payment *records* (not promised disbursements).
- In-job: start/arrival/check-in/task completion, no unsafe quick-complete shortcut, safety exit prominent.

### Admin (web)
Priority sidebar: **Operations** | **Provider verification** | **Safety incidents** | **Payments/reconciliation** | **Policy & regions** | **Reviews** | **Privacy** | **Audit** | **Admin management**, filtered by actual permission.
Dashboard uses actionable queues: urgent incidents first; unfulfilled requests; provider review/expiry; disputed settlements; non-urgent work; system health. Empty queues explicitly "No pending items" with refresh timestamp.

## Screen inventory

### Customer UX (C01–C23; UX names map to existing requirement IDs as needed)
| ID | View | Key elements / action | Backend / availability |
|---|---|---|---|
| UX-C01 | Language and welcome | Sinhala/Tamil/English; reviewed text indicator; simple value explanation | CURRENT; content human-review pending |
| UX-C02 | Phone + OTP | country prefix +94, verification feedback, countdown, accessible error | CURRENT |
| UX-C03 | Home/services | clear category cards, saved service area, active request chip, Help | CURRENT API, UI redesign |
| UX-C04 | Category detail | included tasks, excluded tasks, duration rules, indicative pricing caveat | CURRENT category/policy; no fabricated tariff |
| UX-C05 | Saved locations | Home/Parent's home/etc., area + landmark summary, add | CURRENT |
| UX-C06 | Pin confirmation | map optional; manual address/landmark fallback; show location uncertainty | CURRENT; map limitations |
| UX-C07 | Beneficiary | for me / for family member; relationship and explicit consent explainer | CURRENT |
| UX-C08 | Family member details | contact fields, privacy explanation and consent status | CURRENT; no silent consent |
| UX-C09 | Select tasks | service-specific checklist, short examples, exclusions and optional note | CURRENT |
| UX-C10 | Date/time/duration | Asia/Colombo time explicitly, provider hours constraint, 30-min steps | CURRENT |
| UX-C11 | Quote and terms | itemized amount, validity, cancellations, who receives cash; policy version | CURRENT; price is unapproved until approved |
| UX-C12 | Confirm request | single CTA; loading; idempotency key held; uncertain outcome recovery | CURRENT; align with PR #1 |
| UX-C13 | Beneficiary pending | no dispatch until confirmed; how recipient can approve/decline; refresh | CURRENT |
| UX-C14 | Searching/offer | status timeline and honest explanation; no imaginary ETA | CURRENT |
| UX-C15 | No provider | exact reason or safe fallback; alternative schedule and support | CURRENT request status; fresh quote may be required |
| UX-C16 | Booking summary | assigned provider facts authorized by API, scope, time, exact address to customer | CURRENT |
| UX-C17 | Arrival/check-in | role-limited PIN and explanation, reported status not GPS-derived | CURRENT |
| UX-C18 | Job in progress | visible job state, scoped task checklist, report concern | CURRENT |
| UX-C19 | Completion decision | confirm or dispute, concise outcomes, no automatic completion | CURRENT |
| UX-C20 | Offline payment | reported/confirmed/disputed state, exact counterparty and amount | CURRENT |
| UX-C21 | Review | category-specific rating, moderated text, privacy warning | CURRENT |
| UX-C22 | Help/safety | report problem, status of reports, locally validated emergency guidance | CURRENT skeleton; validation required |
| UX-C23 | Account/privacy | notices, language, family/addresses, personal data export and deletion requests | CURRENT |

### Provider UX (P01–P17)
| ID | View | Key elements / action | Backend / availability |
|---|---|---|---|
| UX-P01 | Language + OTP | same accessible sign-in and recovery | CURRENT |
| UX-P02 | Enrollment overview | eligibility checklist, category choice and estimated review *without invented time* | CURRENT |
| UX-P03 | Personal verification | evidence status, safe capture/upload instructions, expiration | CURRENT |
| UX-P04 | Skill/category approvals | category-specific approved/pending/expired, requirements and remediation | CURRENT |
| UX-P05 | Service area + availability | radius, weekly hours, blacklist dates, conflict warnings | CURRENT |
| UX-P06 | Offers dashboard | available/pending/expired states, auto-refresh, manual refresh | CURRENT |
| UX-P07 | Offer details | time, task list, **approximate** area/distance, net amount if defined, expiry | CURRENT; never show address pre-assignment |
| UX-P08 | Accept/decline | one dominant accept CTA, safe pending state and retry key | CURRENT |
| UX-P09 | My jobs | today/upcoming/completed; honest status and actions | CURRENT |
| UX-P10 | Assigned job | location exact only after assignment; tasks and beneficiary-sensitive minimum | CURRENT |
| UX-P11 | En route / arrived | step-by-step state, no GPS requirement | CURRENT |
| UX-P12 | Secure check-in | beneficiary-held PIN, unsuccessful-attempt feedback, authorized exception path | CURRENT |
| UX-P13 | Work completion | confirm tasks, report unfinished and submit completion | CURRENT |
| UX-P14 | Payment acknowledgement | received / dispute; no false "wallet" or payout balance | CURRENT |
| UX-P15 | Safety exit | immediate protective action, reason, incident pathway | CURRENT |
| UX-P16 | Profile and settings | languages, verification dates, privacy, sign out | CURRENT |
| UX-P17 | Earnings analytics | historical records not hypothetical cash flow | DEFERRED / needs reporting API |

### Admin UX (A01–A13)
| ID | View | Primary information and guard |
|---|---|---|
| UX-A01 | Login, 2FA / step-up | existing admin auth and timeouts |
| UX-A02 | Operations dashboard | urgent incident queue, unfulfilled, assignment monitoring, queue freshness |
| UX-A03 | Request investigation | eligibility rejection reasons, dispatch history, status and interventions |
| UX-A04 | Booking investigation | actor/action timeline, valid transitions, audit/event provenance |
| UX-A05 | Verification queue | pending applications, expiring evidence, reviewer workload |
| UX-A06 | Provider case | scoped identity reveal only after verified step-up/purpose; no PII in generic list |
| UX-A07 | Incidents | urgency, assigned responder, SLA counter, safeguarding notes |
| UX-A08 | Settlements | cash report / provider confirmation / disagreement; finance-only actions |
| UX-A09 | Reviews | privacy-aware moderation |
| UX-A10 | Service regions/policies | drafted vs approved, maker-checker, prohibited tasks visible |
| UX-A11 | Privacy case desk | right-to-access requests, legal holds, retention approval state |
| UX-A12 | Audit and metrics | filterable security/operational events; redacted by role |
| UX-A13 | Admin accounts | permission-bound access, revocations and recovery process |

## Five critical user journeys and state contracts
**J1 Book cleaning for self:** C03->C04->C05/C06->C07(self)->C09->C10->C11->C12->C14->C16->C17->C18->C19->C20->C21. Each stage supports Back, Return, error and accessibility announcements.

**J2 Book for a parent:** C07(family)->C08->C12->C13; explicitly explain that the beneficiary must approve *before* anyone is dispatched. Only beneficiary-appropriate code/identity is sent through the defined consent channel. If declined/expired: no dispatch, clear alternative.

**J3 Provider accepts:** P06->P07->P08->P10->P11->P12->P13->P14. If offer expires or is revoked, show a reason, not "job accepted". If accept POST loses response, verify current offer/booking with same idempotency key; do not create a duplicate.

**J4 No supply:** C14->C15, acknowledge disappointment; offer "Choose a different time" only after ensuring the server supports fresh quote/request; allow support request. No false ETA/progress bar.

**J5 Urgent incident:** Provider P15 or customer C22 -> encrypted report -> admin A07. Urgent cases are prioritized, human resolution required. Do not claim Hela is an emergency response service.

## Component library (original Hela designs)
- **HelaScaffold**: safe areas, accessible title, shared Help access, context-dependent bottom navigation.
- **ServiceTile**: descriptive icon (original licensed), label, "typically takes" only if backed by current policy.
- **ScopeChecklist**: checkable included tasks, prohibited tasks warning, "tell us more" text.
- **AddressCard**: human-readable saved address + landmark, no coordinates on primary screen.
- **BeneficiaryCard**: identity/relationship + "SMS approval required" state.
- **TimeSlotPicker**: calendar + valid times + explicit Sri Lanka timezone.
- **PriceSummary**: itemized LKR with price version + who pays whom + terms.
- **BookingStatusTimeline**: waiting-consent/searching/confirmed/en-route/arrived/in-progress/completed/dispute; only API-confirmed transitions.
- **VerificationStatus**: explicit category, evidence expiry, provenance; no blanket safety claims.
- **OfferCountdown**: read-only time to expiry with accessible announcements, clear accept/decline.
- **OfflineRecoveryPanel**: refresh/retry state; different copy for read vs state-changing operation.
- **SafetyEntry**: clearly visible support/incident contact; never hide under account only.
- **AdminTriageRow**: priority, case ID, last updated, owner, deadline and permission-bound CTA.

## Illustrative mobile layout (not a competitor copy)
```text
┌────────────────────────────────┐
│ Hela Service         Help  EN  │
│ Helping you, where you live    │
│ Home · Kandy              Edit │
├────────────────────────────────┤
│ What help do you need?         │
│ [ Search services...        ]  │
│                                │
│ [Cleaning]  [Errands]          │
│ [Laundry ]  [Kitchen help]     │
│                                │
│ Your next booking              │
│ [No upcoming bookings]         │
│                                │
│ Book help for a family member  │
│ [Choose an address       >]    │
├────────────────────────────────┤
│ [ Home ] [ Bookings ] [Account]│
└────────────────────────────────┘
```
**All text is placeholder English; no fake badges, service availability or prices.** On small devices, allow category cards to stack and long Sinhala/Tamil labels to wrap.

## Design tokens — working proposal, subject to brand approval
- Brand accent candidate: `#0B6E4F` (already used by the customer Flutter theme and admin CSS); not an approved final brand.
- Provider blue `#1F4E79` in existing provider app; prefer harmonized shared neutrals and semantic tokens while retaining role distinction.
- Semantic colors must have text labels/icons; never convey status by color alone.
- 8dp spacing grid, page horizontal 16dp, component corner 12–16dp, cards with subtle borders rather than heavy elevation.
- Base content text >=16sp for primary tasks; secondary >=14sp except captions; support 200% font scaling, no clipping.
- Android tap targets at least 48x48dp with 8dp separation where possible; active status components read by screen readers.
- Fonts use system-supplied Sinhala/Tamil-capable faces (test Noto Sans Sinhala/Tamil rendering, line breaks and glyph widths); reviewed copy required.
- UI must work on 360dp width, low-density network, with no map loaded; use a map as optional location aid not required path.
- States for **every** async screen: first-loading, refresh, empty, offline-read, uncertain-command, validation, permission denied, expired/revoked, normal success.
- Admin web: WCAG 2.2 AA, keyboard navigation, visible focus, accessible table headers, dark/light contrast, responsive triage queue.
- No animation or urgency cues that pressure vulnerable users into approval.

## Research and usability plan
Use synthetic workflows and consented testers only. Each task should have an objective and measure.
1. First-time customer: locate 2-hour cleaning and submit a synthetic booking; measure completion, wrong-tap, time, understanding of confirmation.
2. Overseas booker: schedule for a relative, interpret Sri Lanka timezone and consent; measure whether no dispatch happens before approval.
3. Provider: read one offer and accurately describe tasks, approximate area and price before accepting.
4. Offline case: simulate lost POST response and see whether user checks status instead of double-submit.
5. Admin safety: triage simulated urgent incident within a predefined operational SLA; ensure no unintended disclosure.
6. Accessibility: TalkBack, 200% text, screen rotation, 360dp display, offline/memory stress.
No quantitative usability improvement is claimed until these tests actually run.

## Explicit non-goals
This blueprint does not authorize instant/on-demand bookings, provider bidding, online payment/escrow, GPS tracking, automatic sanctions, healthcare/childcare, unvalidated 24-hour coverage or production launch. Relevant backend/UI expansion needs new requirements, data-protection and business approvals.

## Implementation sequencing
See `ux-implementation-backlog.md`. Build components/styles and add widget tests first; then refactor booking screens without changing API payloads or server state machine; then provider operational UI; then admin dashboard hierarchy; finally reviewed language integration and real-device testing.

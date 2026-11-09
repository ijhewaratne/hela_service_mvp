# Hela Service — UX Implementation Backlog and Acceptance Tests
Date: 2026-11-08 | Baseline: current `main`; PR #1 covers mobile session reliability separately

## Legend
- P0 = first visual redesign without changing current product policies/API; P1 = meaningful UX refinement that may need modest backend changes; P2 = deferred/needs business and safety decisions.
- A feature is **not implemented** merely because it appears in this backlog. All product design must be reconciled against API behavior, synthetic tests and approved policy versions.

## Proposed pull request sequence (separate from already-open reliability PR)
| ID | Priority | Repository areas | Change | Acceptance |
|---|---|---|---|---|
| UX-001 | P0 | `apps/hela_core/lib/src/` | Shared semantic design tokens, `HelaScaffold`, unified loading/empty/error views | Consistent typography, contrast, spacing, 48dp tap targets and 200% scale; mobile widget tests |
| UX-002 | P0 | `apps/customer/lib/main.dart` | Replace plain category ListTiles with clear landing hierarchy, category cards, region selector, active-booking strip, easy Help | Existing category/filter API unchanged; no disabled category shown; long Sinhala/Tamil labels wrap; no fake availability |
| UX-003 | P0 | `apps/customer/lib/booking_flow.dart` | Focused one-step-per-screen booking with visible progress, back action, persistent summary/CTA | Server-side validation retained; all five stages accessible; state preserved on return/rotation; existing tests adapted |
| UX-004 | P0 | `apps/customer/lib/booking_flow.dart` | Status timeline for beneficiary consent, dispatch, success and no supply | Only true server states shown; no live ETA; no auto-confirm; a lost command is described as uncertain |
| UX-005 | P0 | `apps/provider/lib/main.dart`, `jobs.dart` | Clear offer/job cards; countdown, next action, no-availability states, safety entry | Exact address never shown before assignment; rejected offer does not look accepted; acceptance uses key |
| UX-006 | P0 | `apps/provider/lib/profile.dart` | Verification progress/status and actionable missing evidence; service-hours affordance | No unverified "trusted" badge; feedback sourced from eligibility/approval API |
| UX-007 | P0 | `apps/admin/src/pages/Dashboard.tsx`, `styles.css` | Triage-first dashboard with urgent incidents, unfulfilled requests and queue freshness | Permission-filtered, no new PII; keyboard and screen-reader navigation, accessible mobile table |
| UX-008 | P0 | `apps/hela_core/test/`, `apps/customer/test/`, `apps/provider/test/`, admin tests | Golden/snapshot or widget flows with low-width, long text, status edge cases | CI green with existing API tests and new accessibility checks |
| UX-009 | P1 | customer account/booking screens | Family member status, beneficiary consent explanation, date/time clarity for overseas booker | Two actors clearly distinguishable; no dispatch before consent; masked contact details |
| UX-010 | P1 | customer & provider | Better offline/read/uncertain-command recovery and retry affordances | Respect PR #1 client changes; reads safely retried, commands check status and retain idempotency key |
| UX-011 | P1 | admin provider and incident pages | Review workspace with safe escalation, evidence expiration and case audit history | Privileged reveal requires step-up/purpose; urgent events prioritized and logged |
| UX-012 | P1 | localization | Human-reviewed critical English/Sinhala/Tamil strings integrated with version tracking | No legal/consent/safety text silently machine-translated; screenshot/semantics test in all languages |
| UX-013 | P1 | research/QA | Real-device field testing with consented pilot users and a 360dp Android device | Task success, comprehension, navigation mistakes, TalkBack and low bandwidth documented |
| UX-014 | P2 | booking/provider models | Direct provider choice vs auto-matching experiment | Requires approved functional spec, provider exposure rules and backend design; not MVP |
| UX-015 | P2 | services and scheduling | Repeat booking, recurring services, rescheduling, last-minute on-demand | Needs pricing, provider eligibility, cancellation and state machine expansion |
| UX-016 | P2 | location and messaging | Live route/ETA, chat/push | Needs vendor, consent/retention, network and security design |
| UX-017 | P2 | settlement | Online payments, escrow and payouts | Requires full payments, liability, regulatory and reconciliation decision; not offline MVP |

## Mandatory UX non-functional tests
- 360dp Android viewport, font-scale 1.0 / 1.5 / 2.0 and landscape. A home tile or form must not overflow or hide the primary CTA.
- Android touch targets >=48x48dp. Accessibility Scanner/TalkBack on physical device before pilot. Admin web WCAG 2.2 AA, focus/fill and axe.
- UI states: no regions, no categories, no saved addresses, no beneficiaries, zero eligible providers, pending verification, stale/expired offer, declined consent, customer complaint, suspended provider, 429/5xx/offline, delayed SMS and duplicate tap.
- Booking state must be server-backed; never replace `requested` with `accepted` on button press alone.
- Expired price quote must require re-quote. Disable request submission with no tasks or unresolved beneficiary consent as appropriate.
- Sensitive info: no exact customer address before provider assignment; no incident narrative exposed in generic logs or notifications; admin permissions always checked server-side.
- Payment: never imply Hela collected money; both parties have truthful pending/confirmed/disputed state.
- Language: human proofing + assisted tests with native Sinhala/Tamil speakers; no forced all-caps, hardcoded line heights or images of text.
- Performance: design for small Android devices, tolerate intermittent data, avoid map as required dependency, compress licensed original visuals.
- Preserve local-development "SYNTHETIC / NOT FOR REAL DATA" warnings until G2 approval.

## Prioritization rationale
The greatest current UX impact comes from:
1. customer home hierarchy and task finding;
2. making the complex but valid booking steps readable and navigable;
3. clear booking state/consent and provider acceptance confidence;
4. provider and admin operational clarity.

Do **not** replace deterministic eligibility gates, audit trails, idempotency, permission boundaries or manually reviewed safety steps with "smarter UX" automation. A consumer screen must not display features not provided by its server.

## Human research acceptance
Before describing any design as "better", conduct A/B or moderated synthetic usability testing. Proposed metrics (targets require owner approval): booking completion rate, median time to submit, task-selection error rate, wrong-tap rate, number of customer support interactions per 100 bookings, acceptance uncertainty misunderstanding, successful family-consent comprehension, and disabled-provider exclusion rate. Report observed numerators/denominators and methodology; no invented benchmarks.

## First design sprint checklist
- [ ] Approve UX information architecture and supported service categories (design signoff, not legal approval).
- [ ] Add tokens/shared components + Flutter widget/golden coverage.
- [ ] Refactor customer home screen, preserve existing test keys or update assertions deliberately.
- [ ] Refactor booking flow one panel at a time with tests for idempotency and consent.
- [ ] Improve provider offers/job cards and operations dashboard hierarchy.
- [ ] Review with customer/provider/admin pilot representatives using synthetic data.
- [ ] Collect Sinhala/Tamil critical copy review and real-device feedback.
- [ ] Keep PRs small, with CI before merging.

See `competitive-evidence-2026-11-08.md`, `experience-blueprint-v1.md` and `rea-study-protocol.md` for rationale, constraints, and verification procedure.

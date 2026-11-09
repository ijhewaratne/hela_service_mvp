# Hela Service — Competitive Evidence and UX Opportunities
Date: 2026-11-08 | Status: public-source research, **not** REA runtime/APK verification | Product: commercial Hela Service marketplace in Sri Lanka

## Research integrity
- **Observed (PUBLIC-DOC):** what a publisher's public page or support instructions state. This is not firsthand in-app verification.
- **Observed (SOURCE):** facts confirmed in Hela Service repository `main`, baseline `900961478804c823d58f96815e17ff8681a78c16`.
- **PROPOSAL:** our independent feature or UI design; not a claim about any competitor.
- **UNKNOWN:** competitor proprietary implementation, exact server-side rules, verification quality, current live behavior, and published claims not independently tested.
- Do not reuse third-party icons, source code, screenshots, trademarks or marketing copy in Hela Service. This report links public references as research citations.

## Competitive products and concrete learnings
| Product | PUBLIC-DOC evidence | What Hela should independently implement | What to reject/defer |
|---|---|---|---|
| PickMe (Sri Lanka) | Fast pickup/drop-off booking, driver discovery and live tracking. FAQ: Sinhala/Tamil/English; booking for a friend; emergency contacts; cash and card methods. [Official site](https://pickme.lk/), [support](https://pickme.lk/support/), [ride](https://pickme.lk/services/ride/). | Short, location-first booking, clear searching/assigned states, family-member booking status, easy help contact and locally familiar address selection. | Do not show a live GPS/ETA map until real telemetry/routing is implemented and consented. Do not copy taxi-specific time/cancellation rules. |
| Urban Company | Service-specific catalog and package/add-on approach, calendar and slots illustrated publicly. [Cleaning example](https://www.urbancompany.com/dubai-professional-home-cleaning), [design write-up](https://medium.com/uc-design/the-all-new-urban-company-app-32b3953e990e). | Task presets and clear included/excluded scopes; structured selection over a free-text blob; price summary before submitting. | Do not display a promise of instant service without supply and dispatch capacity. Avoid commercial upsell clutter for pilot. |
| Taskrabbit | Category/location-first finding; provider profiles with rates, experience, reviews, availability and booking. [How it works](https://www.taskrabbit.com/how-it-works), [hire instructions](https://support.taskrabbit.com/hc/en-us/articles/46260422073755-How-Do-I-Hire-a-Tasker). | Clear category selection, human-readable task and price, explicit qualifications; future browse-and-select mode only after eligibility and privacy model is approved. | Current Hela dispatch is automatic sequential offers; do not pretend customers can choose an individual provider today. |
| Airtasker | User-written scope/time/budget and interested providers making offers; selection of an offer. [Posting instructions](https://support.airtasker.com/hc/en-au/articles/202427844-How-do-I-post-a-task), [after posting](https://support.airtasker.com/hc/en-gb/articles/204835424-I-posted-my-task-what-s-next). | Useful questions: what, where, when, for whom, price expectations. Repeat a previous task only after consent and correct quote refresh. | Open public task feeds, bid negotiations, and escrow are **not** current Hela R1/R2 functions. Do not turn household addresses into public postings. |
| Care.com | Search by care type, location, one-time/recurring scheduling; provider profiles, reviews and background check explanations. [Finding a caregiver](https://help.care.com/s/article/How-do-I-find-a-caregiver?language=en_US), [background check explanation](https://www.care.com/about/safety/background-checks/). | Family/beneficiary distinction, concise verification evidence and expiration semantics; respectful support copy. | No unapproved regulated clinical/childcare services; do not claim background checks that Hela has not actually performed. |
| Fixie (Sri Lanka) | Website advertises cleaning/cooking/repairs, verification, recurring services and app payment. [Official site](https://www.fixie.lk/), [FAQ](https://www.fixie.lk/faq/). | Direct-local competitor benchmark: service discoverability, repeat booking and visible verification labels. | Do not copy claimed nationwide supply; coverage and safety statements are unverified marketing claims. |
| Blu (Sri Lanka) | Customer help says service choice, provider profiles/pricing, in-app communications, cash/card, and complaints. [Blu help](https://www.blu.lk/help-support), [customer Google Play listing](https://play.google.com/store/apps/details?id=com.blu.service). | Compare clarity of provider identity, quote and support; stage user-chosen providers as a later option if approved. | No payment integration during offline-payment pilot. |
| ServiceOne (Sri Lanka) | Website advertises customer/provider applications, scheduling and history/invoices. [Official site](https://www.serviceone.lk/). | Validate whether simple calendars, booking history and saved locations outperform a generic form. | Do not treat marketing claims as tested UI functionality. |

## Key comparative finding
PickMe is a model for **local familiarity and dispatch feedback**; Urban Company for **task-specific booking**; Taskrabbit for **person-centric trust**; Airtasker for **scope definition**; Care.com for **family context**; Sri Lankan service apps for **competitive market requirements**. Hela's strategic differentiation must be built and tested, not claimed from these references.

## Existing Hela UI inspection (SOURCE, main)
- `apps/customer/lib/main.dart`: three tabs Book / My bookings / Account. Home retrieves regions/categories and renders category ListTiles with duration labels; little service discovery hierarchy, no search, no prominent active-booking entry.
- `apps/customer/lib/booking_flow.dart`: a Flutter `Stepper` with Address -> Beneficiary -> Tasks -> When -> Price/terms. It implements necessary validations, beneficiary consent and idempotency. The presentation could be easier to scan as focused full-screen steps with summary and a persistent next action. This is a UX proposal, not an identified functional defect.
- `apps/provider/lib/main.dart`: tabs Offers / My jobs / Profile; offer cards disclose approximate area/time/tasks/price, not private address before acceptance. The provider's pending approvals and hours are in profile/onboarding. Present a work-oriented dashboard with availability and approval status as future UX improvements.
- `apps/admin/src/App.tsx`, `styles.css`: permission-filtered modules, mostly functional operational tables/cards, 230px side navigation. Improve information hierarchy, alert triage, dense table filters, mobile readability, and clear action priority while retaining backend permissions.
- `apps/hela_core/lib/src/l10n.dart`: English keys and Sinhala/Tamil selection but unreviewed translations fall back to English. Never label legal or safety screens "translated" until human-approved.
- Development-only banners and safety guardrails must remain visible for synthetic deployments.
- Pull request #1 (mobile network/session hardening) is separate work and should not be overwritten by UX changes.

## Design decisions recommended
1. **One clear primary action on each screen.** Home: "Book a service"; Active booking: "View booking"; Provider: "Review offer" or "View today's job"; Admin: priority incident/review item.
2. **Location first, but privacy always.** Saved address cards and area chips; exact address visible only to assigned provider as the server currently enforces.
3. **Guided category-specific booking.** Use a five-stage state contract, but display one stage at a time; clear task scope, address, beneficiary, time and quote; no hidden defaults for a third-party beneficiary.
4. **Honest status.** "Waiting for family confirmation", "Searching", "Offer sent", "Confirmed" only when verified by API; always provide retry, support and alternative scheduling paths.
5. **Verification badges must say what was verified.** E.g. "Identity checked" / "Category approved through [expiry]" only when authenticated API data exists. Never say "background checked" generically.
6. **Design for low-end Android and patchy data.** Lightweight assets, no dependency on a full-screen live map, skeleton/empty/offline states, resumption, explicit uncertain command outcomes.
7. **Languages.** English, Sinhala and Tamil approved text per key and category; flexible multiline layouts, fonts that support glyphs, no text baked into images.
8. **Offline payments at pilot.** Show exact price, who receives it, and two-party acknowledgment state; no misleading "Pay now" CTA.
9. **No TRUST CLG modules, fundraising, Rotary/BNI or donations in the commercial app MVP.** Organizational operations remain separate.
10. **No live location/ETA, public provider choice, escrow, recurring service promise or on-demand promise until implemented, tested and authorized.**

## UX questions for direct user research (not yet performed)
- Can an older adult or first-time smartphone user complete a 2-hour cleaning request?
- Does someone booking from overseas understand local time, address and beneficiary consent?
- Do providers understand that they cannot see the exact address until assignment?
- Does a user understand the difference between a quoted price and a confirmed booking?
- When the network drops after tapping "Confirm", do users wait/check status instead of creating duplicate requests?
- Is a human safety contact discoverable in an active booking within two taps?

## Evidence gaps and verification
No authorized PickMe, Taskrabbit, Urban Company, Fixie or Blu APK has been supplied; no proprietary app has been executed with REA in this study. Public pages and images may lag current releases. A proper reproducible investigation should use authorized test devices/accounts, record app version/date/locale and screen sequence, document uncertainty, and avoid extracting proprietary source or bypassing protection. See `rea-study-protocol.md`.

## UI acceptance gates
- No fake availability, verification, ETA, price approval or payment-success claims.
- All stateful actions use the existing backend protocol; no client-only "accepted/paid" state.
- Minimum 48x48dp interactive targets and ~8dp spacing on Android; keyboard/screen-reader support; 360dp width and 200% text tested. [Android guidance](https://support.google.com/accessibility/android/answer/7101858).
- For admin web, target WCAG 2.2 AA and perform a manual assessment in addition to automated axe tests. [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/).

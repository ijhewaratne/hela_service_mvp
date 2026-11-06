# PickMe → Hela Service Evidence Matrix (as of 2026-11-04)

This is a **public functional benchmark**, not a statement about PickMe's private code. Source links are PickMe-owned public resources unless otherwise stated.

| ID | PickMe public capability | Public evidence | Evidence label | Independent Hela requirement | Confidence |
|---|---|---|---|---|---|
| REF-01 | Pickup/drop-off and estimated fare | https://pickme.lk/ and https://play.google.com/store/apps/details?id=com.pickme.passenger | PUBLIC | Location + service task + transparent estimate | High |
| REF-02 | Nearby available driver assignment | https://www.pickme.lk/ | PUBLIC (high-level claim) | Eligibility-first provider offer and acceptance | High for product concept, **unknown actual algorithm** |
| REF-03 | Real-time driver tracking and ETA | https://pickme.lk/ and https://pickme.lk/services/ride/ | PUBLIC | En-route limited ETA/arrival, not in-home tracking | High |
| REF-04 | Advance booking | https://pickme.lk/services/ride/ | PUBLIC | Scheduled household services | High |
| REF-05 | Booking for a friend | https://pickme.lk/support/ | PUBLIC | Authorized family/beneficiary booking | High |
| REF-06 | Cancellation and rating/support | https://pickme.lk/support/ | PUBLIC | Explicit service cancellation/dispute policies | High |
| REF-07 | Driver app document onboarding | https://pickme.lk/drive/ | PUBLIC | Identity/reference/skills/category verification | High |
| REF-08 | Cash/cashless rides | https://play.google.com/store/apps/details?id=com.pickme.passenger | PUBLIC | Offline settlement reports in initial Hela MVP | High |
| REF-09 | Exact backend stack / database / message queue | Not publicly established | UNKNOWN | Proposed modular FastAPI/PostGIS/Redis stack | Not applicable |
| REF-10 | Exact driver-ranking or surge-pricing formula | Not publicly established | UNKNOWN | Independently specified, auditable matching and transparent pricing | Not applicable |

## Lawful reference-app observation
Use only publicly available content and your own authorized accounts/test journeys. For each screen/flow log **app version, observation date/time, device, locale, scenario, displayed outputs and uncertainty**. Do not inspect other customers' data, bypass authentication/security controls, compromise production systems, or claim private implementation details from observations.

## Observation priority (no need to reproduce exact PickMe UI)
1. Booking entry/navigation and minimum information requirements.
2. Loading/search/offered/accepted/no-match UI states.
3. Scheduled vs immediate booking and cancellation handling.
4. Family/beneficiary booking and location exposure.
5. Support, receipts, ratings and dispute disclosure.
6. Driver registration and what is publicly described about document review.

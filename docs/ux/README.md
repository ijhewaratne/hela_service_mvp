# Hela Service UX Research and Redesign (2026-11-08)

This directory contains proposed **independent** Hela Service UI/UX work derived from public product documentation and repository source inspection. It does not contain REA execution results, competitor decompiled code or copied third-party designs.

Start here:
1. [Competitive evidence](competitive-evidence-2026-11-08.md) — PickMe, Urban Company, Taskrabbit, Airtasker, Care.com, Fixie, Blu and ServiceOne; observations vs proposals.
2. [Experience blueprint](experience-blueprint-v1.md) — end-to-end customer, provider and admin architecture, screen lists, journeys, components and accessibility requirements.
3. [REA study protocol](rea-study-protocol.md) — exact prerequisites, authorized-research boundaries, CLI examples, provenance and evidence ledger.
4. [Implementation backlog](ux-implementation-backlog.md) — staged design PRs with code locations and acceptance criteria.

## Status
- Research of official public documentation: completed 2026-11-08.
- Source review of Hela Service `main` at `9009614`: completed.
- Actual REA runtime operation against PickMe/other competitor APKs: **NOT DONE** (requires the necessary authority, local toolchain, a permitted APK/owned device and a defined data policy).
- UX implementation or real-device usability study: **NOT DONE**; this is a design proposal.
- Hela Service launch clearance: **NOT GIVEN**. Current commercial MVP has no donation/CLG integrations and uses offline payment records; safety, privacy, verification and release gates remain in force.

Keep research and proposed screenshots separate from direct observation. Do not merge visually attractive changes that weaken provider eligibility, beneficiary consent, idempotency, RBAC, audit or data minimization.

## Reviewer starting points
- Customer: `apps/customer/lib/main.dart`, `booking_flow.dart`
- Provider: `apps/provider/lib/main.dart`, `jobs.dart`, `profile.dart`
- Admin: `apps/admin/src/App.tsx`, `styles.css`, `pages/`
- Shared: `apps/hela_core/lib/src/`
- Backend authority: `services/api/`; do not invent API contracts in UI prototypes.

**Next coding PR recommendation:** `UX-001` shared UI foundation and `UX-002` customer home hierarchy, accompanied by 360dp/200%-text widget tests. Keep code separate from this documentation PR and from the existing network/session reliability PR.

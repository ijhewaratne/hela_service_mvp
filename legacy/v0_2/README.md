# Hela Service — Synthetic Marketplace MVP (v0.2)

**Independent engineering prototype for Hela Service / TRUST.**

> **DANGER: LOCAL SYNTHETIC DEMO ONLY.** This project deliberately uses
> `X-Demo-Actor` to impersonate fictional actors. It has **no real authentication**,
> payment gateway, completed provider verification, operating safeguards, privacy controls,
> or production-grade deployment configuration. Do not put it on a public server,
> use real personal data, or provide access to real customers/providers.

This code demonstrates the first *vertical slice* identified in `Hela_Service_PRD_v1.md`:

1. A fictional household creates a 2-hour cleaning request.
2. The backend filters providers by active status, approved category, unexpired
   credentials, safeguards, service radius, free schedule, and absence of conflicting jobs.
3. An admin dispatches time-limited offers to the two highest-ranked eligible providers.
4. A provider accepts. A **single SQLite `BEGIN IMMEDIATE` write transaction** arbitrates
   concurrent acceptances so only one booking is assigned in this **single-node demo**.
5. The provider moves through the booking state machine; the customer confirms completion.
6. Offline payment acknowledgements and append-only event/audit logs are recorded.

This is an **independently designed Hela Service proof of concept**, not PickMe software or its private API.

## Requirements

- Python 3.11 or newer
- Internet only for first-time dependency installation (unless dependencies are cached)
- Local machine; test with synthetic fixtures only

## Run locally (macOS / Linux)

```bash
cd hela_service_mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export HELA_DEMO_MODE=1
uvicorn hela.asgi:app --host 127.0.0.1 --port 8000
```

Navigate to [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
The interactive API docs show the endpoints. For authenticated demo calls, add:
`X-Demo-Actor: customer_asha` (or another synthetic actor) to the request headers.
For booking creation, offer acceptance, and status transitions, add a unique
`Idempotency-Key: any-long-unique-key-001` header.

For Windows PowerShell use `.venv\\Scripts\\Activate.ps1` and `$env:HELA_DEMO_MODE='1'`
before running `uvicorn ... --host 127.0.0.1`.

**Full walkthrough, no server required:**

```bash
python -m scripts.run_demo
```

**Run automated tests:**

```bash
python -m pytest -q
```

## Fictional actors (not genuine identities)

| Header `X-Demo-Actor` | Role | Purpose |
|---|---|---|
| `customer_asha` | customer | Owns `addr_asha` |
| `customer_nimal` | customer | Owns `addr_nimal` |
| `provider_alpha` | provider | Approved, available cleaning + errands |
| `provider_beta` | provider | Approved, available cleaning + errands |
| `provider_gamma` | provider | Approved, available cleaning + errands |
| `provider_unverified` | provider | Unapproved: intentionally nearest, but excluded |
| `provider_suspended` | provider | Suspended, always excluded |
| `provider_expired` | provider | Expired category evidence, always excluded |
| `provider_far` | provider | Outside service radius, always excluded |
| `admin_ops` | admin | Dispatch, audit and category review |

All sample addresses are fabricated points near Kandy. They do not identify real households.
All seeded service rates, e.g. LKR 1,200/hour for cleaning, are **test values**,
not researched local market prices or an approved TRUST pricing decision.

## Basic API sequence

`POST /v1/requests` → admin `POST /v1/admin/requests/{id}/dispatch` →
provider `GET /v1/offers/me` → `POST /v1/offers/{id}/accept` →
`POST /v1/bookings/{id}/transitions` → customer offline payment report →
provider offline payment acknowledgement → customer completion.

The request body uses an owned address ID, enabled category, task details and two
ISO8601 datetimes with explicit timezone offsets. The demo accepts bookings
between 5 minutes and 60 days in the future, up to 8 hours long.

## Key engineering choices

- **Hard eligibility gates before scoring.** The nearest unverified provider cannot win.
- **Revalidate at acceptance.** Expired/revoked approvals and competing assignments block acceptance.
- **Deterministic ranking.** Uses straight-line distance as a *proxy*, slot slack,
  synthetic decline rate and synthetic workload penalty; it does **not** claim a
  driving ETA or PickMe's actual matching formula.
- **Single-winner assignment.** Database uniqueness + serialized SQLite writers protect
  this local prototype. Production requires PostgreSQL transaction/locking design,
  exclusion constraints and race/load testing.
- **Request idempotency.** Same customer + key + body returns the same request;
  altered body with same key produces HTTP 409.
- **Booking transition idempotency.** Same actor + booking + key + payload returns
  the saved transition response; altered payload with the same key produces HTTP 409.
- **Event trail and RBAC.** Active synthetic role gate, owner checks, immutable-by-API
  booking events and admin event logging. Not full production authentication/audit tamper protection.
- **No funds collected.** Customer-reported offline payment followed by provider
  acknowledgement. Commission is zero until a real policy is approved.
- **High-risk services disabled.** Elder companionship, child supervision and clinical
  care are *not* enabled in this prototype.

## Implemented API subset

`/health`, `/v1/categories`, `/v1/addresses/me`, `/v1/requests`,
`/v1/requests/{id}`, `/v1/admin/requests/{id}/dispatch`, `/v1/offers/me`,
`/v1/offers/{id}/accept`, `/v1/offers/{id}/decline`, `/v1/bookings/{id}`,
`/v1/bookings/{id}/events`, `/v1/bookings/{id}/transitions`,
`/v1/bookings/{id}/settlement`, the two settlement acknowledgement routes,
`/v1/bookings/{id}/incidents`, admin category approval, audit and matching logs.

`generated_openapi.json` contains the routes generated from **this running code**.
The prior pack's `openapi.yaml` is a **future contract proposal**, not an exact match.

## What is NOT built and why

**Not operational:** customer/driver mobile apps, Sinhala/Tamil/English interface,
consent and beneficiary delegation, real identity verification, provider documents,
background/reference verification, live GPS, road routing, notifications, persistent
provider availability management, real payments, refunds, human safeguarding case
workflow, monitoring, fraud controls, enterprise audit retention and legal signoff.

**Production engineering prerequisites:** OAuth/OIDC or validated OTP + session security;
MFA and independent admin role controls; secure document storage; data minimization,
privacy notices/consent; encryption and secrets management; rate limiting;
PostgreSQL/PostGIS + carefully reviewed transaction semantics; provider overlap
constraints; real credential-validity policy; an incident escalation owner;
commission/contract/insurance design; observability, backups, recovery tests and
independent security assessment. **No live users until these are addressed.**

## Repository layout

```text
hela/                 FastAPI implementation, matching, schema and synthetic fixtures
scripts/run_demo.py   Full end-to-end simulation without running a server
tests/                Automated safety, ownership, concurrency and workflow tests
.github/workflows/    Ready-to-copy test workflow (not uploaded to GitHub)
ENGINEERING_NOTES.md  Current vs planned scope, production gates and next build tasks
generated_openapi.json  API specification emitted from FastAPI
```

## Ownership and operations

TRUST's social-purpose programs, donations, beneficiaries and commercial Hela Service
bookings must retain separate ledgers and appropriate governance. The legal entity,
brand, data controller roles and pilot location have not yet been finalized in code.

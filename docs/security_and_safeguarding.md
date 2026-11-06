# Security and Safeguarding Baseline — Hela Service

The primary risks are not only cyberattacks. They also include improper entry to homes, unreliable worker verification, exposure of household locations, treatment of vulnerable people, identity fraud, disputed cash settlement and false confirmation of service.

## Threat/control matrix
| Threat or failure | Preventive control | Detection & response |
|---|---|---|
| Fake or unqualified provider | Manual identity/references/category checks; no auto-approval | Periodic re-verification, blocked expired credentials |
| Account takeover / OTP abuse | Rate limits, device/session controls, secure OTP expiry, no OTP logging | Alert on velocity/anomalies; lock and recover |
| Customer address disclosure | Exact address only to eligible assigned worker at appropriate stage | Access logs and incident review |
| Provider impersonation at doorstep | Photo and identity display, booking code, arrival confirmation | Customer support and emergency guidance |
| Unsafe category deployment | Disabled by default pending category policy and staffing | Admin feature-flag kill switch |
| Double booking / conflicting acceptances | SQL transactions, locks, unique active assignment, offer TTL | Conflict alerts and audit events |
| Forged status or settlement | Server-enforced transitions, actor permissions, dual acknowledgments | Reconciliation and dispute queue |
| Admin misuse of identity documents | Least privilege, MFA, expiring signed URLs, audit reads | Access review, anomaly alerts |
| Harassment or harm in home | Verifiable identity; clear conduct policy, safe exit/incident processes | Immediate human triage; suspend offers without automatically assuming guilt |
| Continuous location surveillance | Do not track during in-home work; limit live route updates | Regular data-minimization and retention audit |
| Leaked sensitive images | Encrypted restricted buckets; content-type and malware checks | Security incident response and removal procedures |
| Cross-border processing and transfers | Vendor mapping, processing agreements, legal assessment | Data inventory and compliance review |

## Operational safety conditions before launch
- Named accountable safety owner, clear support contact and hours, incident escalation procedure.
- Provider approval is category-specific and evidence-based; badges accurately reflect the checks completed.
- Users can report urgent concerns without going through a star-rating flow.
- No clinical, emergency or childcare services until regulatory/insurance/training and safeguarding readiness are reviewed.
- Keep explicit booker versus beneficiary authorization and household privacy rules.
- Assess Sri Lankan PDPA as amended, using current DPA commencement notices; core provisions begin 2027-01-01 according to DPA's 2026 gazette. Do not use superseded older dates.

## Never allow an AI agent to
- Approve providers, dismiss safeguarding incidents, override mandatory eligibility, disclose private documents, mark payment settled, or make a medical/safety judgment without a defined human authorization process.
- Invent confidence in missing credentials or assume silence equals acceptance.

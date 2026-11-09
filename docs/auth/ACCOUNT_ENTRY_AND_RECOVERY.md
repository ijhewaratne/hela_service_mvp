# Hela Service — Account Entry, Registration and Recovery
Status: working engineering implementation on `feat/auth-role-entry-20261109`; **not yet public-release approved**.
Date: 2026-11-09.

## Why this change exists
The earlier implementation had three isolated entrypoints: `apps/customer` (phone OTP), `apps/provider` (phone OTP and application), and `apps/admin` (username + password + TOTP). These were technically functioning but gave a first-time user **no clear choice of role, no visible account-creation path, and no account-access explanation**. Such an interface is incomplete even when the underlying API is tested.

## Primary mobile app (apps/customer)
The default `lib/main.dart` now runs `HelaApp`, which provides:
1. Language selection, then "How would you like to use Hela?"
2. **Book a service** -> customer sign-in or create-account choice -> SMS OTP -> booking/account UI.
3. **Offer my services** -> same phone verification identity -> provider application and human eligibility checks; never auto-approved.
4. **I am receiving a service** -> explain independent SMS approval/decline before dispatch. There is no invented recipient account, password, or booking access token.
5. **Administrator** -> a distinct secure web portal (credentials + TOTP + scoped permissions). The app can open a configured HTTPS console URL, but role choice itself grants nothing.
6. Registered customer/providers can switch roles without re-verifying the same active session, while server-side authorization is always enforced.

### Account creation and phone recovery
`POST /v1/auth/otp/request` and `POST /v1/auth/otp/verify` are the only existing customer/provider authentication methods. The backend creates a **customer identity automatically** when a new phone number verifies, regardless of whether the user started at the "Sign in" or "Create account" button. This is explicitly explained in the UI; the separate buttons are **onboarding intents, not distinct server endpoints**.

The "Create account" journey requires an acknowledgement of the auto-registration behavior, then SMS verification. A first-time profile setup can save an optional public display name using `PATCH /v1/me`; a provider's legal ID, date of birth, application, documents and category approvals remain in the separately secured workflow.

No customer/provider password exists. Therefore a "Forgot password" submission would be misleading. Instead the UI supports change-number/resend-code flow and explains that a **lost phone/SIM requires a reviewed support-led account recovery procedure** (future operational/security work; not currently a self-service feature).

### Admin account access
The admin console is separate from the consumer mobile app and uses a provisioned username + password + authenticator code. The admin login page now shows a supervised recovery explanation. **There is no publicly accessible admin signup or password-reset endpoint.** Implementing one needs token lifecycle, anti-enumeration, MFA reset governance, a verified second channel, audit, abuse protection and operational owner approval.

To enable the external portal button when building the unified mobile entrypoint:
```bash
cd apps/customer
flutter run --dart-define=HELA_ADMIN_PORTAL_URL=https://your-approved-admin-domain.example
```
An unset or non-HTTPS URL displays a non-interactive configuration message. Do not substitute an unapproved address or mix admin credentials into consumer OTP tokens.

## Tests and security invariants
- Show four role choices without making a network call or creating any account.
- Recipient and admin selections cannot cause a customer OTP signup or grant a provider/admin permission.
- New phone verification shows registration notice; profile completion uses existing `PATCH /v1/me`.
- Provider selection after sign-in does not bypass the server verification workflow.
- Sign in retains a passwordless OTP path; account recovery copy must not claim a password exists.
- Existing session rotation, revocation, consent, privacy, beneficiary acknowledgement and booking safeguards must remain intact.
- Real-world consent/notice review, account recovery SOP, Sinhala/Tamil copy review, carrier/SMS delivery and Android/TalkBack verification remain G2 blockers.

## Related code
- `apps/customer/lib/main.dart`: unified primary HelaApp entrypoint, preserved CustomerApp for dedicated tests.
- `apps/customer/lib/entry.dart`: role chooser, user-journey explanations, profile setup and admin redirect.
- `apps/customer/pubspec.yaml`: dependency on existing provider Flutter package + external URL launcher.
- `apps/hela_core/lib/src/widgets.dart`: transparent OTP sign-in/account-creation UI.
- `apps/admin/src/pages/Login.tsx`: administrator recovery instructions without unsafe self-provisioning.
- `apps/customer/test/role_entry_test.dart`: new end-to-end widget checks against a synthetic API.

This change is deliberately not an authorization to deploy real-user traffic or collect real IDs. See `docs/pilot/G2_RELEASE_READINESS.md` for remaining production gates.

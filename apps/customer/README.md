# Hela Service — Primary Flutter App (Synthetic Development)

The default `flutter run` entrypoint is now **`lib/main.dart` → `HelaApp`**.
This app opens with language selection followed by a role/journey chooser.

## Journeys
- **Book a service:** phone OTP → customer home, bookings, addresses, beneficiaries, privacy and support.
- **Offer services:** same phone OTP → apply as a provider or access your verified provider profile/jobs. The backend still controls provider eligibility.
- **Receiving a service:** understand that a beneficiary must approve their booking by SMS before dispatch. There is no independent recipient account or portal yet.
- **Administrator:** opens a *separate* staff web portal if an approved HTTPS URL has been configured. User self-selection never creates a privileged account.
- **Create account:** uses OTP for a new phone number. The current backend automatically creates a customer user during the first successful verification, even on the "Sign in" path. The app explicitly discloses this and offers optional display-name setup.

Customer/provider accounts are **passwordless**; changing/recovering a lost phone number requires a properly designed account recovery procedure. Admin login **does** use password + authenticator and is handled in `apps/admin`.

## Run locally
```sh
cd apps/customer
flutter pub get
flutter run --dart-define=HELA_API_BASE=http://YOUR-DEV-API:8000
```
The API environment variable must match `apps/hela_core/lib/src/config.dart`.
For the separate administrative web console, supply its **real, approved HTTPS address** as:
```sh
flutter run --dart-define=HELA_ADMIN_PORTAL_URL=https://your-approved-host.example
```
A missing or non-HTTPS URL intentionally displays an unavailable configuration message rather than a fake link.

## Test
```sh
cd apps/customer && flutter analyze && flutter test
cd apps/provider && flutter analyze && flutter test
cd apps/admin && pnpm typecheck && pnpm test
```

Development and automated tests use synthetic identities, services and regions. **Do not enter real identities or deploy to public traffic.**

## Still not implemented for a production pilot
Approved SMS vendor; secure lost-number recovery; staff-run admin credential recovery; human-reviewed Sinhala/Tamil text; real-device accessibility and mobile installation QA; operator-approved legal/pricing/safeguarding policies; monitoring and backup verification. See `docs/pilot/G2_RELEASE_READINESS.md` and `docs/auth/ACCOUNT_ENTRY_AND_RECOVERY.md`.

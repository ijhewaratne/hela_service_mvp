# Hela Service apps (R2, synthetic development only)

| App | Path | Stack | Screens |
|---|---|---|---|
| Customer | `apps/customer` | Flutter, Android (minSdk 26) | C01–C13 |
| Provider | `apps/provider` | Flutter, Android (minSdk 26) | P01–P10 |
| Shared | `apps/hela_core` | Dart package | API client, sessions, localisation, map pin, shared widgets |
| Operations console | `apps/admin` | React + TypeScript + Vite | A01–A10 |

## Run against the local API

```bash
# API and worker (see the root README), then:
cd apps/admin && pnpm install && pnpm dev                  # http://localhost:5173

cd apps/customer
flutter run --dart-define=HELA_API_BASE=http://10.0.2.2:8000 \
            --dart-define=HELA_DEV_INBOX_TOKEN=<the token the API was started with>
```

`10.0.2.2` is how the Android emulator reaches this machine. Debug builds may use plain HTTP to that address
only (`src/debug/res/xml/network_security_config.xml`); release builds require TLS. In debug builds,
`HELA_DEV_INBOX_TOKEN` adds a "fill code from sandbox inbox" button to sign-in. Release builds ignore it.

## Security and privacy choices

- **Mobile sessions:** the access token lives in memory only. The refresh token is kept in Android Keystore-backed storage (`flutter_secure_storage`). A refresh only happens on `TOKEN_INVALID`, and only one at a time, because the server revokes the whole session if a rotated refresh token is replayed.
- **Admin sessions:** tokens are kept in memory only, so reloading the page signs the admin out. They're sent as Bearer headers, so there are no cookies and CSRF doesn't apply. The API allows only the configured admin origins (CORS).
- **Duplicates and uncertain outcomes:** state-changing commands send an `Idempotency-Key`. Each app keeps it across retries to prevent duplicate requests or bookings (AT-020). When a command's response is lost, the app reports an *unknown outcome*, not proof that nothing happened, and asks the user to check the current status before retrying. A failed read (loading a screen) changed nothing, so it just says there is no connection.
- **Temporary authentication outages:** refresh timeouts, 429s and 5xx errors preserve the existing secure refresh token and allow a later retry. On app startup, both apps show a dedicated connection-retry screen instead of treating an outage as an invalid login. Explicit refresh-token rejection still signs the user out. The client does not replay the original command if refresh was unavailable. "Sign in with a different number" on that screen ends the old session on the server too (`POST /v1/auth/revoke`, which needs only the refresh token). If the server can't be reached, the token is kept aside in secure storage and revoked as soon as a connection works again.
- **Honest states:** "Accepted" appears only once the server has a booking (NFR-08). An empty search says no provider was found, never a made-up provider.
- **Address privacy:** providers see the exact address only for an accepted, active job. Offers show the area and a distance band.
- **Language:** Sinhala and Tamil are selectable, but no translations exist yet. Until human-reviewed text is available, those languages show English with a visible "translation pending review" notice (Q61). Safety, consent and legal text is never machine-translated.
- **Maps:** development uses OpenStreetMap tiles with attribution. The production tile and routing vendor is pending (DEC-08). If the map can't be used, coordinates can be typed in by hand.

## Tests

```bash
cd apps/hela_core && flutter test     # client: token refresh, idempotency, offline handling, formatting
cd apps/customer && flutter test      # full booking journey against a fake API
cd apps/provider && flutter test      # onboarding; offer -> lost response -> same booking -> PIN check-in
cd apps/admin && pnpm test            # client behaviour and permission-filtered navigation
```

## Not done in R2 yet

- Translations (Q61), and accessibility testing with TalkBack and large text (NFR-12).
- Push notifications: the SMS sandbox stands in until a vendor is chosen (DEC-08).
- iOS builds (Q72: Android first).
- Real geocoding or address search (Q17).
- En-route location tracking (Q46).

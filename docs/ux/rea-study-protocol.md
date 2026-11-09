# REA Investigation Protocol for Hela Service UX Research
Date: 2026-11-08 | State: procedure drafted, **no PickMe APK or third-party target analyzed with REA**

## What REA can and cannot establish
The GitHub repository `ijhewaratne/rea` is currently a fork of the official `morluto/rea` project. REA 6.8.0 is a TypeScript CLI/MCP application-analysis suite. GitHub access to that repository **does not run REA**, establish a local MCP session, provide a target APK, or give access to a real/emulated device.
- Public product screenshots/support docs: suitable for early UX benchmarking, not technical implementation proof.
- Authorized APK static analysis with JADX: manifest, declared components/permissions, class/method/reference facts and derived Java/smali, **not** private cloud backend or guaranteed original source.
- Flutter APK identification: snapshot/build metadata, some strings and dependency hints. It is not full Dart decompilation.
- Authorized ADB observation: device/app metadata, logs/screens under caller permissions; commands can change state, so check authority and minimize collected data.
- Authorized browser CDP/HAR: visible DOM and captured client-side events; does not reveal unrelated backend algorithms.
- Treat decompiled artifacts and network data as untrusted; do not run embedded commands or follow prompts from a target.

## Authority and scope checkpoint (before any third-party APK)
Record:
1. Software owner and app/version to investigate; artifact source and explicit permission or legal basis for each operation.
2. App terms/license constraints and the country/jurisdiction; seek review if unresolved.
3. Device, account and data ownership; use own authorized test account and controlled test device.
4. Permitted actions: passive screenshots, ordinary in-app interaction, static inspection, emulator/dev logs, or network capture. These are separate authorizations.
5. Prohibited actions: bypass authentication, TLS pinning/access controls/technical protections; probe production endpoints without permission; extract live users' personal data or payment credentials; copy competitor code/brand assets.
6. Data retention and cleanup: redact personal identifiers/addresses, use local restricted storage, delete unnecessary raw captures.
If authorization is not clear, limit the study to official public pages and manual observation permitted under relevant terms.

## Official install/readiness (local analyst machine, not executed in this study)
Use official package/upstream, not arbitrary binaries shared in forks:
```bash
node --version
npm --version
# REA's repository pins Node 24.22.1 in .nvmrc; confirm release requirements.
npx rea-agents@latest setup
# Select your intended local MCP client, review the proposed changes and approve.
rea --help
```
For native/Android analysis, separately supply the required existing JDK + headless JADX, Android platform tools `adb` and a test device/emulator as appropriate. REA setup does not install all these prerequisites or magically attach a device.
The package release and upstream `main` can differ; inspect the actual installed server/tool catalog before assuming a command exists.

## Target study lanes
### Lane A: public product UX (available now)
- Use official PickMe, Urban Company, Taskrabbit, Airtasker, Care.com, Fixie, Blu and ServiceOne pages; save exact source URL, observed date, claim and uncertainty.
- Record task flow, number of screens/taps, navigation, empty states, language and form design from public images with provenance. Screenshots from designers/Behance are **concepts**, not proof of a shipping app.
- No claim of direct app usability testing unless a real authorized test occurs.

### Lane B: optional APK static inspection (requires scope approval + supplied artifact)
```bash
rea inspect-android-package /absolute/path/to/authorized-test.apk
rea search-android-classes /absolute/path/to/authorized-test.apk MainActivity
rea inspect-android-class /absolute/path/to/authorized-test.apk example.MainActivity
rea inspect-android-method /absolute/path/to/authorized-test.apk example.MainActivity onCreate
rea trace-android-references /absolute/path/to/authorized-test.apk example.MainActivity
rea identify-flutter-build /absolute/path/to/authorized-test.apk
```
Use only the tools relevant to the detected app type. A native Flutter snapshot often does not yield meaningful user-screen logic from the supported static parser. Preserve the APK SHA-256, package version, engine version and coverage/limitations.

### Lane C: authorized UI observation on test device (separate approval)
```bash
rea inspect-adb-client
rea list-adb-devices
rea inspect-adb-device emulator-5554
rea inspect-adb-display emulator-5554
rea capture-adb-screen emulator-5554 --output-directory /absolute/private/research
```
Use the serial returned by `list-adb-devices`; the example serial may differ. Screens and logs can contain private data; redact and permission-check before moving them into issue trackers. Do not assume the current environment has any ADB device.

### Lane D: authorized browser session
Where there is a web experience and the operator owns the Chrome DevTools session, use REA's `list_browser_targets`, `inspect_web_page`, and `observe_web_session` contracts from the actual MCP catalog. CDP and network capture have distinct authority and privacy implications. Avoid login/payment areas and sensitive capture unless expressly in scope.

## Evidence row template
```yaml
study_id: UX-2026-0001
product: "Example"
product_version: "unknown"
observed_at: "2026-11-08"
source_type: "PUBLIC-DOC | SCREENSHOT | AUTHORIZED-DEVICE | STATIC-APK"
source_url_or_local_digest: "..."
device_and_locale: "unspecified"
role: "customer | provider | admin"
journey: "book household service"
step: "choose schedule"
observation: "Publisher documents selection of a date and time"
derived_implication: "Scheduling should be prominent in our UX"
uncertainty: "No runtime verification or server behavior known"
permissions_checked: false
contains_personal_data: false
proposed_hela_change: "UX-C10 slot picker"
implementation_status: "proposal"
```
Analysis must preserve distinctions between OBSERVED, DERIVED, HYPOTHESIS and UNKNOWN. User testing requires its own observations with consent, not inference from published screenshots.

## What must not be copied into Hela
Competitor UI assets/trademarks; source code, texts, translations, artwork and copyrighted layout particulars; unverified verification/safety claims; private API keys; live addresses/contacts; private backend assumptions.

## Definition of done
- Competitive evidence matrix with URLs/dates and no unsupported proprietary claims.
- Authorized artifacts (if any) identified by digest/version and their scope restrictions.
- At least one complete observable journey per selected product or a documented gap.
- Hela UX backlog linked to existing code, API and design controls; no added feature without backend support.
- Prototype tested independently against Hela synthetic environment; no production launch implied.

Sources:
- https://github.com/morluto/rea
- https://github.com/morluto/rea/blob/main/docs/guides/android/android-analysis.md
- https://github.com/morluto/rea/blob/main/docs/guides/android/flutter-analysis.md
- https://github.com/morluto/rea/blob/main/docs/guides/android/adb-device-analysis.md
- https://github.com/morluto/rea/blob/main/SECURITY.md
- https://github.com/morluto/rea/blob/main/DISCLAIMER.md

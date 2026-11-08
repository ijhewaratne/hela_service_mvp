#!/usr/bin/env bash
# End-to-end tests of the real Flutter apps against the real local API and worker (synthetic data only).
# Runs the apps as macOS desktop builds, so no Android emulator or phone is needed. The same Dart code runs on
# Android; device-only behaviour (TalkBack, Android Keystore, push, the camera) still needs a real device.
#
# Prerequisites: the dev stack, API and worker from README "Run locally", started with a sandbox inbox token, and
# the synthetic provider from the M02/M03 walkthroughs (approved for household cleaning, with an area and hours).
#
#   HELA_DEV_INBOX_TOKEN=<the API's sandbox inbox token> scripts/app_e2e.sh [customer|provider|all]
#
# Each test signs the synthetic provider in with a real OTP, so a full run sends that number two codes. The API
# allows three per number per 15 minutes (HELA_OTP_MAX_SENDS_PER_WINDOW): run again after the window, or start the
# local dev API with a higher limit for repeated runs. Never raise it outside a local dev API.
set -euo pipefail
cd "$(dirname "$0")/../apps"

: "${HELA_DEV_INBOX_TOKEN:?set HELA_DEV_INBOX_TOKEN to the token the dev API was started with}"
API="${HELA_API_BASE:-http://127.0.0.1:8000}"
curl -sf "$API/health" >/dev/null || { echo "API not reachable at $API" >&2; exit 1; }

# The app window must be able to draw: with the display asleep or the screen locked, macOS stops delivering
# frames and the test would stall (each test has its own time limit, so it then fails). caffeinate keeps the
# display awake for the duration of the run.
run() {
  (cd "$1" && caffeinate -dim flutter test "integration_test/$2" -d macos \
     --dart-define "HELA_API_BASE=$API" --dart-define "HELA_DEV_INBOX_TOKEN=$HELA_DEV_INBOX_TOKEN")
}

case "${1:-all}" in
  customer) run customer live_booking_test.dart ;;
  provider) run provider live_job_test.dart ;;
  all) run customer live_booking_test.dart && run provider live_job_test.dart ;;
  *) echo "usage: $0 [customer|provider|all]" >&2; exit 2 ;;
esac

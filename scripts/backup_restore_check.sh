#!/usr/bin/env bash
# AT-026 / NFR-06 drill: dump the dev database, restore it into a scratch database, and prove the copy is
# complete and untampered (row counts, schema version, sealed audit chain). Local synthetic data only.
# Production needs continuous WAL archiving / PITR to reach the RPO <= 15 min design goal; a nightly logical
# dump alone cannot.
set -euo pipefail
CONTAINER=${HELA_PG_CONTAINER:-hela-dev-postgres-1}
SOURCE=${1:-hela}
TARGET=hela_restore_check
psql_() { docker exec -i "$CONTAINER" psql -U hela_owner -v ON_ERROR_STOP=1 -tA "$@"; }

# Report the outcome to monitoring (HelaBackupFailed / HelaBackupStale alerts). Set HELA_RECORD_HEARTBEAT=0 to skip.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
heartbeat() {
  [ "${HELA_RECORD_HEARTBEAT:-1}" = "0" ] && return 0
  (cd "$ROOT/services/api" && "${HELA_PYTHON:-.venv/bin/python}" -m hela.cli record-heartbeat --name backup \
      --status "$1" --detail "$2") || echo "warning: could not record the backup heartbeat" >&2
}
trap 'rc=$?; [ $rc -ne 0 ] && heartbeat failed "restore check exited with $rc"' EXIT

TABLES="users sessions provider_profiles provider_evidence provider_category_approvals service_requests offers bookings
booking_assignments booking_events settlements incidents audit_logs audit_chain"
counts() { for t in $TABLES; do printf '%s=%s ' "$t" "$(psql_ -d "$1" -c "SELECT count(*) FROM $t")"; done; }

start=$(date +%s)
docker exec "$CONTAINER" pg_dump -U hela_owner -Fc -d "$SOURCE" -f /tmp/hela_backup.dump
dump_done=$(date +%s)
psql_ -d postgres -c "DROP DATABASE IF EXISTS $TARGET WITH (FORCE)" >/dev/null
psql_ -d postgres -c "CREATE DATABASE $TARGET OWNER hela_owner" >/dev/null
docker exec "$CONTAINER" pg_restore -U hela_owner -d "$TARGET" --no-owner --exit-on-error /tmp/hela_backup.dump
end=$(date +%s)

src=$(counts "$SOURCE"); dst=$(counts "$TARGET")
src_ver=$(psql_ -d "$SOURCE" -c 'SELECT version_num FROM alembic_version')
dst_ver=$(psql_ -d "$TARGET" -c 'SELECT version_num FROM alembic_version')
chain=$(psql_ -d "$TARGET" -c "SELECT coalesce(audit_verify_chain()::text, 'intact')")
size=$(docker exec "$CONTAINER" stat -c %s /tmp/hela_backup.dump)

echo "dump: $((dump_done - start)) s, restore: $((end - dump_done)) s, total: $((end - start)) s, size: $size bytes"
echo "schema: source=$src_ver restored=$dst_ver"
echo "audit chain in restored copy: $chain"
if [ "$src" = "$dst" ] && [ "$src_ver" = "$dst_ver" ] && [ "$chain" = "intact" ]; then
  echo "RESTORE CHECK PASSED"; echo "$dst" | tr ' ' '\n' | sed '/^$/d'
else
  echo "RESTORE CHECK FAILED"; echo "source:   $src"; echo "restored: $dst"; exit 1
fi
psql_ -d postgres -c "DROP DATABASE $TARGET WITH (FORCE)" >/dev/null
docker exec "$CONTAINER" rm -f /tmp/hela_backup.dump
heartbeat ok "dump+restore $((end - start)) s, $size bytes"

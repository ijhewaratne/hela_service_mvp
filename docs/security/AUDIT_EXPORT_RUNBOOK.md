# Audit export runbook (Q41, Q64, NFR-12)

**Status:** built and tested against local MinIO with synthetic data. The bucket, its retention period (DEC-09)
and the storage provider (DEC-08) are not decided; nothing is exported anywhere outside this machine.

## Why

The database already makes the audit log append-only and seals it into a hash chain (migration 0005). That stops
the application role from changing history, but someone with **owner** access to the database could edit rows
and recompute every hash, and the in-database check would still pass. Copying each sealed row to storage that
refuses overwrites and deletes closes that gap: a later rewrite no longer matches the export.

## What is exported

`python -m hela.cli export-audit` seals any pending rows, then writes every sealed row after the last exported
one as a new pair:

| Object | Content |
|---|---|
| `segments/00000001.jsonl` | One sealed row per line: position, hashes, and exactly the fields the chain hash covers |
| `manifests/00000001.json` | Row count, first/last position and hash, the segment's SHA-256, and the previous manifest's SHA-256 |

A manifest is written only after its segment. Manifests chain to each other, so removing or replacing a segment is
detected even without the database. Rows hold pseudonymous ids and scrubbed details only (phone numbers, codes,
tokens and addresses are never written to the audit log), so the export adds no new personal data.

If the database chain no longer continues from the last export, the run **fails** instead of starting a fork, and
the `HelaAuditExportFailed` alert fires.

## Targets

* `--bucket NAME`: an S3-compatible bucket **with Object Lock enabled** (a bucket without it is refused). Every
  object is written with `If-None-Match: *` and an Object Lock retention date. Use `--mode compliance` outside
  development: in governance mode an account with bypass permission can still delete.
* `--dir PATH`: a local directory for development. Files are created exclusively and read-only, but a directory
  is **not** write-once storage.

On a versioned (Object Lock) bucket a plain DELETE does not remove the locked object; it hides it behind a delete
marker. An ordinary listing would then show a shorter export that still looks valid. The exporter and verifier
therefore read object versions: they use the original version of each key and report any delete marker or
second version. Export refuses to run while such a sign is present.

## Credentials

The scheduled job (`infra/deploy/kubernetes/base/audit-export-cronjob.yaml`) uses its own S3 identity
(Secret `hela-audit-export`), allowed only `PutObject`, `GetObject`, `GetObjectRetention`, `ListBucket` and
`ListBucketVersions` on the export bucket. It must not have `DeleteObject`, `PutObjectRetention`,
`PutBucketObjectLockConfiguration` or `BypassGovernanceRetention`. Reading uses the runtime database role, which
can read sealed rows but cannot change them.

## Checking an export

```bash
python -m hela.cli verify-audit-export --bucket hela-audit-export --against-db
```

Without `--against-db` it checks the export alone: every manifest is present and in sequence, each chains to the
previous one, each segment matches its SHA-256, and every row's hash is recomputed from its content. With
`--against-db` it also confirms that every exported row is still in `audit_chain` with the same hash.

| Message | Meaning |
|---|---|
| `rows differ from what was exported` | The database chain was rewritten after export. Treat as a security incident; the export is the record to trust |
| `exported rows are missing from audit_chain` | Rows were removed from the database, or the database was restored from an older backup (check which first) |
| `deleted (a delete marker hides it ...)` / `has N versions` | Someone tried to delete or overwrite the export. The locked originals are still readable and are what the verifier uses |
| `content does not match its hash`, `does not chain` | An export object was altered; only possible on a target without Object Lock |

## Monitoring

The export records the `audit_export` heartbeat. `HelaAuditExportFailed` (critical) fires when the latest run
failed; `HelaAuditExportStale` (warning) fires when there has been no successful run for 3 hours. The job runs
hourly.

## After a database restore

A restore from backup rolls `audit_chain` back. The export then holds rows the database no longer has, and
`--against-db` reports them as missing. That is expected after a restore: record the restore as an incident
action, and start a new export prefix (for example `--bucket` on a new bucket) so the old export stays intact as
the record of what happened before the restore.

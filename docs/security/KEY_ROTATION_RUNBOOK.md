# Key rotation runbook (Q38, Q65)

**Status:** engineering procedure for the synthetic environment; the owner of the key policy must approve it.
Two key sources exist: `HELA_KEY_PROVIDER=local` (plaintext keys in environment variables, development only and
refused in production) and `HELA_KEY_PROVIDER=transit` (keys wrapped by a key service, below). Which key service
runs in production is DEC-08.

## What is keyed

| Key | Used for | Where it is named | Rotation |
|---|---|---|---|
| Field keys (`HELA_FIELD_KEYRING`) | AES-256-GCM encryption of personal fields and of the per-file data keys for evidence objects | Every ciphertext starts with `v2`, then the key id | Supported, no downtime |
| Token signing keys (`HELA_JWT_KEYRING`) | Ed25519 (EdDSA) signatures on 10-minute access tokens | The token's `kid` header | Supported, no downtime |
| Lookup keys (`HELA_LOOKUP_KEYRING`) | Keyed HMACs used to find an account by phone number, and to check OTP and beneficiary-consent codes | A stored hash is `<key id>:<hex>`; bare hex means the legacy key `k1` | Supported, no downtime (dual lookup) |

Evidence files themselves are encrypted with a random data key per file. Rotating the field key re-wraps those data
keys; the stored files do not have to be rewritten.

## Keyring formats

```json
HELA_FIELD_KEYRING = {"active": "k2", "keys": {"k1": "<base64 32 bytes>", "k2": "<base64 32 bytes>"}}
HELA_LOOKUP_KEYRING = {"active": "l2", "keys": {"k1": "<base64 32 bytes>", "l2": "<base64 32 bytes>"}}
HELA_JWT_KEYRING   = {"active": "j2", "keys": {"j2": "<base64 32-byte Ed25519 seed>"},
                      "verify_only": {"j1": "<base64 32-byte Ed25519 public key>"}}
```

Key ids are 1–32 characters: lowercase letters, digits, `_` and `-`. If `HELA_FIELD_KEYRING` is not set, the
single `HELA_FIELD_KEY_B64` is used as key `k1`; likewise `HELA_LOOKUP_KEY_B64` for lookups. Malformed keyrings stop the service at start-up. In staging and
production the committed development keys are refused.

Generate fresh material (printed once to the operator's terminal; put it straight into the secret manager):

```bash
python -m hela.cli generate-keys --key-id k2
```

## Rotating the field key

1. Add the new key to the ring **and** make it active, keeping the old key:
   `{"active": "k2", "keys": {"k1": "...", "k2": "..."}}`. Deploy the API and the worker. From now on, new values
   are written with `k2`, and old values are still readable with `k1`.
2. Re-encrypt stored values, as the schema owner (`HELA_MIGRATION_DATABASE_URL`):

   ```bash
   python -m hela.cli rotate-field-keys --batch 200
   ```

   It works in small transactions with `SKIP LOCKED`, so it is safe to run while the service is live, and it can be
   stopped and run again at any time. Exit code 3 means some rows were locked by live traffic: run it again.
   It writes one `keys.field_rewrapped` audit entry with the row counts. Plaintext stays in process memory.
3. Check that nothing is left under the old key:

   ```bash
   python -m hela.cli key-status
   ```

   `Values under other keys: 0` means the old key can be removed.
4. Remove `k1` from the ring and deploy. **Before removing a key, make sure no backup you may need to restore
   still depends on it.** A backup taken before step 2 needs the old key to be read. Keep retired keys in the
   secret manager, disabled, for as long as the backup retention period (DEC-09).

Append-only history tables (`settlement_events`, `incident_actions`, `privacy_request_events`) stay append-only
during rotation. Migration 0008 allows one narrow exception: the table owner, with `hela.key_rotation = on` set in
its own transaction, may replace `*_enc` values only. Any change to another column, any removal of an encrypted
value, and any delete are still refused. The runtime role has no UPDATE grant on these tables at all.

`tests/test_key_rotation.py` fails if an encrypted column is added to the schema without being registered in
`hela/security/rotation.py`.

## Rotating the lookup key

A lookup hash cannot be decrypted, so it cannot be re-wrapped; it is recomputed from the value it stands for.

1. Add the new key and make it active, keeping the old one:
   `{"active": "l2", "keys": {"k1": "...", "l2": "..."}}`. Deploy the API and the worker. New hashes are written
   with `l2`. Every lookup tries all keys in the ring (`= ANY(...)`), so existing accounts, OTP codes already sent
   and pending beneficiary-consent codes keep working. OTP send limits count codes made under either key.
   An account found under the old key is moved to the new one when its owner next signs in; a second account is
   never created for the same number.
2. Recompute the remaining account hashes from the encrypted phone numbers, as the schema owner:

   ```bash
   python -m hela.cli rotate-lookup-key --batch 200
   ```

   Same batching and `SKIP LOCKED` behaviour as field rotation; exit code 3 means run it again. It writes one
   `keys.lookup_reindexed` audit entry.
3. Short-lived hashes (OTP challenges, consent codes, the dev SMS inbox) are not recomputed; they expire. Wait at
   least one OTP send window (`HELA_OTP_SEND_WINDOW_SECONDS`, 15 minutes) and the consent lifetime, then run
   `key-status`. `Live lookup hashes under other keys: 0` means `k1` can be removed from the ring.
4. Remove the old key and deploy. Backups taken before step 2 hold account hashes under the old key: after a restore
   from one, put the old key back in the ring (inactive) and run `rotate-lookup-key` again.

**If a lookup key is compromised:** the hashes alone do not reveal phone numbers without the key, but with it an
attacker can test guessed numbers. Rotate as above without waiting, and also rotate the field key if it may have
been exposed at the same time.

## Rotating the token-signing key

1. Generate a new key (`generate-keys`). Set it as the active signing key and move the old key to
   `verify_only` (public key only):
   `{"active": "j2", "keys": {"j2": "<seed>"}, "verify_only": {"j1": "<public>"}}`. Deploy.
2. Wait at least one access-token lifetime (10 minutes, `HELA_ACCESS_TOKEN_MINUTES`). Tokens signed with `j1` keep
   working until they expire, and clients pick up `j2` tokens on their next refresh. Refresh tokens are opaque and
   stored server-side, so they are not affected.
3. Remove `j1` from `verify_only` and deploy.

**If a signing key is compromised:** skip the waiting period. Deploy the new key without the old one in
`verify_only`, then revoke sessions if needed. Every client gets `TOKEN_INVALID` once and refreshes; a stolen token
signed with the old key stops working immediately.

## Keys wrapped by a key service (`HELA_KEY_PROVIDER=transit`)

With `transit`, the keyrings keep exactly the same JSON shape and key ids, but each key is a **wrapped** value
(`vault:v1:...`) encrypted by a key-encryption key (KEK) inside a Vault-compatible transit engine: OpenBao, or
HashiCorp Vault. Each process unwraps the keys once at start-up and keeps them only in memory. Every wrapped key is
bound to its purpose and id (associated data `hela:field:k2`), so a wrapped lookup key cannot be used as a field
key and a key cannot be relabelled. Plaintext keys in a ring are refused. If the key service refuses or cannot be
reached, the API and worker do not start, so revoking the services' access to the KEK is the kill switch for new
processes.

Settings: `HELA_TRANSIT_ADDR` (HTTPS outside dev/test), `HELA_TRANSIT_TOKEN` or `HELA_TRANSIT_TOKEN_FILE`,
`HELA_TRANSIT_MOUNT` (default `transit`), `HELA_TRANSIT_KEY` (default `hela-keys`), `HELA_TRANSIT_CA_FILE`.

**Set up the key service** (once, by the key-service administrator):

```bash
bao secrets enable transit
bao write -f transit/keys/hela-keys type=aes256-gcm96
# Policy for the Hela services: use the KEK, nothing else. No export, no deletion, no key configuration.
bao policy write hela-keys - <<'POLICY'
path "transit/encrypt/hela-keys" { capabilities = ["update"] }
path "transit/decrypt/hela-keys" { capabilities = ["update"] }
POLICY
```

Bind that policy to the API, worker and CLI identities, preferably with Kubernetes authentication and an agent
sidecar that writes the token to `HELA_TRANSIT_TOKEN_FILE` (read on every call, so token renewal needs no restart).
Rotating the KEK (`transit/keys/hela-keys/rotate`) is an administrator action the services cannot perform.

**New keys:** `python -m hela.cli generate-keys --key-id k2 --wrapped` prints only wrapped values and the Ed25519
public key. Add them to the rings as described in the rotation sections above; the steps do not change.

**Moving from local keys:** with `HELA_KEY_PROVIDER=local` still set and the `HELA_TRANSIT_*` settings added, run
`wrap-keyring --name field`, then `--name lookup` and `--name jwt`. Each prints the same keyring, with the same key
ids, wrapped, so existing data stays readable. Store the three outputs, switch to `HELA_KEY_PROVIDER=transit`,
deploy, then delete the plaintext keys from the secret manager. Treat the old plaintext keys as exposed for as long
as backups or old secret versions hold them, and rotate field, lookup and signing keys afterwards.

**Rotating the KEK:** the administrator rotates `hela-keys`; then `rewrap-keyring --name field|lookup|jwt` prints
each ring wrapped under the new KEK version. The transit `rewrap` endpoint does not accept associated data, so the
CLI unwraps and wraps again in its own memory; plaintext is never printed. Deploy the new values. Old KEK versions
can be retired (`min_decryption_version`) once no deployed or backed-up secret still uses them.

Tested against OpenBao 2.4.3 locally (`docker compose -f infra/docker/compose.yml up -d openbao`) by
`tests/test_key_service.py`, which CI runs with the key service required.

## Other key services

A cloud KMS (AWS KMS, Google Cloud KMS, Azure Key Vault) fits the same `KeyWrapper` interface in
`hela/security/transit.py` (`wrap`, `unwrap`, `rewrap`), using Encrypt/Decrypt with an encryption context in place
of associated data. Only the transit adapter has been built. Signing tokens inside the key service, instead of
unwrapping the Ed25519 seed into memory, is a possible later hardening.

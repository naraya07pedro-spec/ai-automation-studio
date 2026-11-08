# Encrypted project/Agent scoped memory v1 · owner opt-in (Stage 136)

The local Agent/project-scoped note store now has a **separately keyed AES-256-GCM mode** for note TEXT. This is an actual encryption transform integrated into the existing SQLite `LocalScopedAgentMemory` API. It preserves note revision, digest, generation, expiry and original Agent/project scope semantics used by approved plans.

## Security boundary

- An encrypted note's ciphertext is authenticated with random 12-byte nonces and additional authenticated data binding its project, Agent, note ID, revision, generation, provenance kind/label and expiry. Metadata tampering, wrong keys, or invalid ciphertext block startup/reads with a generic failure and no plaintext leak.
- An encrypted store cannot be opened by the plaintext mode; an existing plaintext store cannot be used in encrypted mode before explicit offline migration. It never silently falls back to plaintext. New encrypted databases are possible with zero legacy notes.
- Encryption **covers only the `scoped_agent_memory.text` column**. Other fields, SQLite schema, profile instructions, local chat/legacy memory cards, plan snapshot/context, model prompts, audit ledgers, provider receipts and filesystem backups are NOT encrypted by this feature. It is NOT whole-disk encryption or a defense against a compromised running OS.
- A previous plaintext backup, SQLite journal, OS swap, filesystem snapshot and deleted SSD sectors can still retain original note content after migration. We do not claim cryptographic erasure or independent backup/restore proof.
- `requirements-vault.txt` pins PyCA cryptography for the AESGCM implementation. Without the dependency the encrypted mode **fails closed** rather than substituting a homegrown cipher.

## Explicit key provisioning

Generate a new independent 32-byte base64url key **in a private terminal**, using the existing audited `generate_master_key()` helper from `app/local_connector_vault.py`. Keep it separately backed up off-device. Do **not** paste a live key in chat, shell history, a GitHub issue, code, screenshots, or logs.

For local Windows use, provision `AAS_SCOPED_MEMORY_KEY` in a trusted, temporary process-start secret mechanism before launching. For systemd Linux deployments, provision `aas-scoped-memory-key` through `LoadCredential` as an owner/root private file, and do not also set its environment-variable counterpart. The private credential allowlist is extended accordingly. This is a **different key** from the managed connector vault master key.

To start a new encrypted-scoped-memory local profile:

```powershell
# Run only after provisioning a separate AAS_SCOPED_MEMORY_KEY privately.
# Requires local Ollama + plans + scoped note feature flags.
.\START_AI_STUDIO.cmd --mode full --enable-encrypted-scoped-memory
```

Unspecified mode stays backward-compatible **plaintext**, for existing owners. The Workspace capability display explicitly reports whether project/Agent note text is encrypted.

## Migration of existing plaintext notes

**Do not activate encryption against an existing plaintext database until migration is done.** Migration is destructive to the plaintext live copy and is never automatic. The operator must first have an **independently tested encrypted backup** and private key recovery plan, and the server must be stopped.

```powershell
# Owner-only offline CLI, NOT an HTTP endpoint.
python app/migrate_scoped_memory_encryption.py --migrate
```

The CLI checks the real owner's current password, takes the standard localhost port lock, requires the exact typed phrase `MIGRATE SCOPED MEMORY`, and reads the separately provisioned secret. It rejects nonexistent, malformed, mixed or already encrypted datasets. SQL `BEGIN IMMEDIATE` updates all live notes or none. On success it compacts the SQLite database with `VACUUM`, then reopens all scopes with production decryption checks and verifies the row count. It writes redacted audit outcomes. Afterward start with the encrypted launch flag.

If the operation reports an uncertain storage/verification status, **do not retry blindly or delete anything**. Stop the server, inspect the encrypted backup and live SQLite state, and preserve the existing key. Migration preserves note generation, revisions and metadata: references remain valid only if existing plan authorization conditions still hold.

## Explicit non-goals

- No key rotation or recovery from a lost private key in v1. Losing the key loses encrypted notes unless a verified external recovery exists.
- No background monitoring, paid provider call, email send, VPS provisioning or external connector access.
- No guarantee of certified data erasure from SSD, old backups or any other local ledger.
- No claim of live owner Windows/device acceptance. CI tests use disposable SQLite, synthetic notes, owner-migration gate and pinned local AESGCM without real owner credentials.

See `tests/test_scoped_memory_encryption.py` and `.github/workflows/scoped-memory-encryption.yml`.

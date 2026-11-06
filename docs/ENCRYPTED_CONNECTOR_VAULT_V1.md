# AES-256-GCM Encrypted Connector Vault V1 · 2026-11-06

This is the **optional encrypted storage mode for already-supported managed HTTPS/n8n connectors**, not a complete multi-provider OAuth/ChatGPT plugin marketplace. It uses the audited PyCA \`cryptography\` library's AESGCM primitive, with a separately supplied independent random 256-bit master key. There is no custom-designed cipher and **no fallback to plaintext when unlock fails**.

## Why there are two modes

Previous releases persisted managed webhook URLs and optional signing secrets in owner-only plaintext files. This release preserves the old \`--enable-managed-webhooks\` startup choice for compatibility, but it is **NOT encrypted**. The new \`--enable-encrypted-managed-webhooks\` mode uses a different encrypted file at \`~/.ai-automation-studio/managed-webhooks-vault.json\`. Its registry contains authenticated AES-256-GCM ciphertext, a fresh 96-bit nonce per write, and non-secret algorithm/version metadata; it does not contain webhook paths or HMAC signing secrets in plaintext.

The new mode still uses the existing per-connector SQLite delivery ledger in \`~/.ai-automation-studio/managed-webhook-ledgers/\`. **This preserves prior no-retry evidence across migration** and prevents the same connector/event from being silently re-sent just because its credential storage changed. You cannot run plaintext and encrypted manager modes together in one server process.

The master key is **never auto-generated inside the server or written next to the encrypted registry**. You must generate, store and supply the SAME key for every restart. Losing it makes encrypted connections inaccessible. A wrong key, altered ciphertext or missing library fails closed and does not import the legacy plaintext file.

## Install and configure on Windows

Open PowerShell in the repository directory, and install only the optional vault dependency:

\`\`\`powershell
git pull
python -m pip install -r requirements-vault.txt
\`\`\`

Generate a new master key once on your own computer:

\`\`\`powershell
python tools/migrate_managed_webhook_vault.py --generate-key
\`\`\`

Store that printed random key in your own private password manager or a separate secure backup. Do **not** paste it in ChatGPT, GitHub, a public terminal transcript, or a screenshot. Do not store it in the same folder or backup as the encrypted vault.

In the PowerShell session you will use to launch Studio, enter the existing saved key when prompted:

\`\`\`powershell
$env:AAS_VAULT_MASTER_KEY = Read-Host "Paste your private vault master key"
.\START_AI_STUDIO.cmd --mode offline --enable-encrypted-managed-webhooks
\`\`\`

After login, section **08 · Managed connections** reports \`AES-256-GCM\` server-side storage and supports registering/revoking connections, exact immutable event previews and one owner-confirmed HTTPS POST. The full secret/path never appears in normal list, history, preview or status API responses. Each action retains the previously implemented owner login/CSRF and current-password gates.

The startup key is passed through a process environment variable. A malicious process running under the same OS account or a privileged administrator may read process memory or environment; **AES-256-GCM at rest does NOT guarantee security against an already-compromised host**. The app does not have a hardware KMS/HSM, automatic key rotation or a provider OAuth authorization broker.

## Upgrade existing plaintext registrations

If you already used the old \`--enable-managed-webhooks\` mode, **stop the Studio server first**. With the same already saved vault key set in your PowerShell environment and no existing encrypted vault file, run:

\`\`\`powershell
python tools/migrate_managed_webhook_vault.py --migrate
\`\`\`

The offline tool refuses to overwrite an existing encrypted vault and refuses to run while the default Studio port is occupied. It reads the existing owner-only registry, encrypts its complete list, flushes/syncs the vault file, authenticates it by decrypting it back, and **then** removes the old active plaintext pathname. It keeps the exact existing connector IDs and prior SQLite delivery ledgers. Once migration succeeds, launch only with \`--enable-encrypted-managed-webhooks\`.

**Deleting the plaintext filename does NOT securely erase old SSD blocks, filesystem snapshots, backup archives, cloud backups, or other copied credentials.** Inspect old backups; rotate high-value webhook paths and signing secrets at their provider if they may have been exposed. Do not manually delete or replace your encrypted vault without backing up its key and understanding current delivery ledger state. If an encrypted vault already exists, this migration intentionally refuses to merge/overwrite it.

The separate encrypted file may be created empty by first launching encrypted mode; if you intend to migrate from legacy mode, run the migration **before** your first encrypted launch.

## Linux private VPS

The same \`requirements-vault.txt\` dependency and \`--enable-encrypted-managed-webhooks\` flag apply. Supply \`AAS_VAULT_MASTER_KEY\` through an operator-controlled private service environment file with root/service-account permissions. Do not put the key in public GitHub, container images, source code, an exposed browser input, or application log lines. Do not expose port 8765. The user's Tencent VM, its OS, capacity and running state have NOT yet been verified, so this documentation is **not deployment evidence**.

## Security boundary and verification

- **Cryptography:** AES-256-GCM authenticated encryption through PyCA \`cryptography==50.0.2\` with 32 random key bytes, fresh OS-generated 12-byte nonce per write and fixed versioned Additional Authenticated Data. The whole credential registry is encrypted; only envelope metadata is plaintext. Incorrect key, missing dependency, malformed or changed ciphertext all fail closed.
- **Storage durability:** writes use a random private temporary file, flush/fsync, OS-private mode 0600 on POSIX, and atomic replace. On Windows, inherited NTFS ACLs are not verified by these operations. Protect the master key and old backups separately.
- **Availability:** current mode is a local server that runs only while the Python process is alive. Encrypted files do not deploy anything to Tencent Cloud or make the service 24/7.
- **Permissions:** model-selected free-form URLs, arbitrary remote tools, automatic managed-connector schedule sends, Gmail/WhatsApp/HubSpot/OAuth and generic MCP plugin installation remain **not implemented**. The separate legacy scheduled n8n autopilot is NOT automatically migrated or granted access by this vault.
- **Threat limitations:** AES-GCM does not inherently detect restoring an older authentic vault ciphertext (rollback). Key in process env is not an HSM. Tamper/incorrect-key detection is not the same as host compromise prevention. No claims of cryptographic proof against malware.
- **Tests:** focused tests verify actual encrypted ciphertext, absence of plaintext URL/secret strings, wrong-key and tamper refusal, distinct nonce per update, preserved one-send history and offline migration. A dedicated CI job installs PyCA and executes those tests. The regular stdlib-only tests skip this optional dependency only if it is absent, so **cryptography CI** is necessary before claiming validation.
- **Live acceptance pending:** no actual user n8n endpoint or Tencent VPS has been verified. Production data should not be entrusted to this code solely because synthetic/CI checks are green.

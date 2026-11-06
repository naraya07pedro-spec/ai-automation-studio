"""Offline migration: plaintext owner webhook registry -> authenticated AES-GCM.

Requires --migrate and AAS_VAULT_MASTER_KEY; refuses when the app's default
loopback server is running. No secrets leave the computer. The old SQLite
delivery ledgers are SHARED across modes to preserve no-retry protection.
Deleting the plaintext filename is NOT secure deletion of SSD snapshots.
"""
import argparse
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_connector_manager import LocalConnectorManager, ManagedConnectorError
from local_connector_vault import (
    EncryptedConnectorVault, VaultError, decode_master_key, generate_master_key)


def migrate(home, key):
    decode_master_key(key)
    home=Path(home).expanduser()
    legacy=home/"managed-webhooks.json"
    target=home/"managed-webhooks-vault.json"
    if not legacy.is_file() or legacy.is_symlink():
        raise VaultError("legacy_registry_missing_or_unsafe")
    if target.exists() or target.is_symlink():
        raise VaultError("encrypted_vault_already_exists_refuse_overwrite")
    # Refuse if a server is listening on the normal private Studio port.
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as lock:
        try:
            lock.bind(("127.0.0.1",8765))
        except OSError:
            raise VaultError("stop_local_studio_server_before_migration") from None
        legacy_manager=LocalConnectorManager(None,legacy)
        try:
            entries=[dict(item) for item in legacy_manager._entries]
        finally:
            legacy_manager.close()
        vault=EncryptedConnectorVault(target,key)
        vault.write({"version":1,"entries":entries})
        verify=vault.read()
        if verify != {"version":1,"entries":entries}:
            raise VaultError("encrypted_migration_verification_failed")
        # Delete the old active plaintext pathname only after encrypted data
        # was durably committed and authenticated on read-back.
        legacy.unlink()
        return len(entries)


def main(argv=None):
    parser=argparse.ArgumentParser(description="Local offline encrypted connector vault tools")
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--generate-key",action="store_true",
                         help="Print one random base64url 256-bit vault master key. Save OFFLINE; do not share.")
    actions.add_argument("--migrate",action="store_true",
                         help="Migrate legacy registry after stopping Studio. Removes plaintext filename but cannot securely wipe SSD backups.")
    args=parser.parse_args(argv)
    if args.generate_key:
        print("Store this key in a private password manager OFF this device; it cannot be recovered from the vault:")
        print(generate_master_key())
        return 0
    key=os.environ.get("AAS_VAULT_MASTER_KEY","")
    try:
        count=migrate(Path.home()/".ai-automation-studio",key)
    except (VaultError,ManagedConnectorError,OSError) as exc:
        raise SystemExit("Migration refused: "+str(exc)) from None
    print("Encrypted registry verified. Migrated connection records:",count)
    print("Legacy plaintext file pathname deleted. SSD blocks, backups and snapshots may STILL contain plaintext.")
    print("Use --enable-encrypted-managed-webhooks for all future launches.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())

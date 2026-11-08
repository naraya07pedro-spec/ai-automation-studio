"""Verify an encrypted scoped Agent note is recoverable from an owner backup.

This is read-only and OFFLINE. It does not modify or restore owner state.
The backup passphrase and the independently provisioned scoped-memory key
must BOTH work. No key or note plaintext is printed or written to disk.
"""
from contextlib import closing
from datetime import datetime
import getpass
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from offline_owner_backup import inspect as inspect_archive, BackupError
from local_scoped_memory_crypto import ScopedMemoryCipher, ScopedMemoryCryptoError
from local_scoped_agent_memory import (
    _note_id, _scope, _text_valid, HEX32, SOURCE_KINDS,
    ScopedMemoryError)
from local_service_credentials import read_runtime_secret, ServiceCredentialError

DATABASE = "scoped-agent-memory.sqlite3"
MAX_NOTES = 200


class RecoveryProofError(RuntimeError):
    pass


def verify_scoped_notes_archive(archive, passphrase, private_key):
    """Prove actual decrypt+schema of EVERY scoped note in the sealed backup.

    An empty backup cannot establish key recovery, and is not reported PASSED.
    A wrong key or ANY plaintext/malformed note is fail-closed.
    """
    if type(passphrase) is not str or type(private_key) is not str:
        raise RecoveryProofError("recovery_secrets_required")
    # Existing backup inspection authenticates AES-GCM envelope, validates
    # owner verifier, digest manifest, and all embedded SQLite quick_checks.
    try:
        files = inspect_archive(Path(archive), passphrase)
        raw = files.get(DATABASE)
        if raw is None:
            raise RecoveryProofError("scoped_memory_database_absent_from_archive")
        cipher = ScopedMemoryCipher(private_key)
        with closing(sqlite3.connect(":memory:")) as database:
            database.deserialize(raw)
            if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RecoveryProofError("scoped_memory_sqlite_integrity_failed")
            rows = database.execute("""SELECT project_id,agent_id,note_id,
                text,revision,generation,source_kind,source_label,expires_utc
                FROM scoped_agent_memory ORDER BY project_id,agent_id,note_id
                LIMIT ?""", (MAX_NOTES + 1,)).fetchall()
            if not rows:
                raise RecoveryProofError("no_encrypted_notes_to_prove_recovery")
            if len(rows) > MAX_NOTES:
                raise RecoveryProofError("scoped_note_limit_exceeded")
            scopes = set()
            for project,agent,note,stored,revision,generation,kind,label,expiry in rows:
                try:
                    _scope(project,agent)
                    _note_id(note)
                    if (type(revision) is not int or revision < 1
                            or type(generation) is not str
                            or HEX32.fullmatch(generation) is None
                            or type(kind) is not str or kind not in SOURCE_KINDS
                            or type(label) is not str or len(label) > 96
                            or any(ord(ch) < 32 or ord(ch) == 127 for ch in label)
                            or type(expiry) is not str):
                        raise ValueError
                    parsed = datetime.fromisoformat(expiry)
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        raise ValueError
                    if not cipher.is_sealed(stored):
                        raise RecoveryProofError(
                            "plaintext_or_mixed_scoped_backup_not_recovery_proven")
                    plaintext = cipher.unseal(
                        stored,project,agent,note,revision,generation,
                        kind,label,expiry)
                    if not _text_valid(plaintext):
                        raise RecoveryProofError(
                            "decrypted_scoped_note_semantics_invalid")
                except (ScopedMemoryError, ValueError, TypeError,
                        ScopedMemoryCryptoError):
                    raise RecoveryProofError(
                        "scoped_backup_metadata_or_decryption_failed") from None
                scopes.add((project,agent))
    except RecoveryProofError:
        raise
    except (ScopedMemoryCryptoError, ScopedMemoryError, ValueError, TypeError):
        raise RecoveryProofError("scoped_backup_metadata_or_decryption_failed") from None
    except (BackupError, sqlite3.Error, OSError, ServiceCredentialError):
        # Do not reveal paths, ciphertext, SQL engine diagnostics or secrets.
        raise RecoveryProofError("scoped_backup_authentication_or_database_failed") from None
    return {
        "status":"ENCRYPTED_SCOPED_NOTES_RECOVERABLE_FROM_ARCHIVE",
        "notes_decrypted_and_validated":len(rows),
        "scopes_validated":len(scopes),
        "archive_authenticated":True,
        "independent_scoped_key_verified":True,
        "plaintext_written_to_disk":False,
        "live_owner_state_modified":False,
        "physical_off_device_copy_verified":False,
        "external_credentials_recovered":False,
        "actual_owner_device_restore_tested":False,
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="Read-only check: archived encrypted Agent notes + separately held key.")
    parser.add_argument("--archive", required=True,
                        help="Existing AES-GCM .aasbak archive (not a live SQLite path)")
    args = parser.parse_args(argv)
    try:
        passphrase = getpass.getpass("Offline backup passphrase: ")
        # Runtime credential has a separate allowlisted name, never the
        # connector vault master key. Avoid passing secrets as CLI args.
        key = read_runtime_secret("AAS_SCOPED_MEMORY_KEY")
        if not key:
            raise RecoveryProofError(
                "separate_AAS_SCOPED_MEMORY_KEY_unavailable")
        outcome = verify_scoped_notes_archive(args.archive,passphrase,key)
        print(json.dumps(outcome,sort_keys=True))
        return 0
    except ServiceCredentialError:
        print("RECOVERY_PROOF_BLOCKED: scoped_memory_key_credential_unavailable",file=sys.stderr)
        return 2
    except RecoveryProofError as exc:
        print("RECOVERY_PROOF_BLOCKED: "+str(exc),file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

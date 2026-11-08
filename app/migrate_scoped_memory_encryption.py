"""Offline, owner-gated, all-or-nothing plaintext -> AES-GCM scoped note migration.

No HTTP, provider or automatic background operation. SQL transaction covers all
live note rows. Does NOT securely erase historical backups, SSD snapshots,
filesystem journals, SQLite free pages or process memory.
"""
import argparse
import getpass
from pathlib import Path
import socket
import sqlite3

from local_operator_auth import LocalOperatorAuth
from local_private_sqlite_path import prepare_private_sqlite
from local_scoped_agent_memory import _text_valid, LocalScopedAgentMemory
from local_scoped_memory_crypto import ScopedMemoryCipher
from local_service_credentials import read_runtime_secret
from local_security_audit import LocalSecurityAudit


class MigrationBlocked(RuntimeError):
    pass


def migrate_encrypted_notes(database, master_key, *, acknowledged=False):
    """Only callable after independent owner authorization and backup review."""
    if acknowledged is not True:
        raise MigrationBlocked("explicit_migration_approval_required")
    dbfile = Path(database).expanduser()
    if not dbfile.is_file() or dbfile.is_symlink():
        raise MigrationBlocked("existing_scoped_memory_database_required")
    path = prepare_private_sqlite(dbfile)
    cipher = ScopedMemoryCipher(master_key)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN IMMEDIATE")
        try:
            records = db.execute("""SELECT project_id,agent_id,note_id,text,
               revision,generation,source_kind,source_label,expires_utc
               FROM scoped_agent_memory ORDER BY project_id,agent_id,note_id""").fetchall()
            if not records:
                raise MigrationBlocked("no_plaintext_notes_to_migrate")
            if len(records) > 200:
                raise MigrationBlocked("scoped_memory_capacity_exceeded")
            # No partial conversion: reject any sealed or malformed row up-front.
            if any(ScopedMemoryCipher.is_sealed(row[3]) for row in records):
                raise MigrationBlocked("already_encrypted_or_mixed_database")
            if any(not _text_valid(row[3]) for row in records):
                raise MigrationBlocked("scoped_memory_invalid_plaintext")
            for project,agent,note,text,rev,gen,kind,label,expiry in records:
                envelope = cipher.seal(text,project,agent,note,rev,gen,kind,label,expiry)
                changed = db.execute("""UPDATE scoped_agent_memory SET text=?
                  WHERE project_id=? AND agent_id=? AND note_id=?
                  AND revision=? AND text=?""",
                  (envelope,project,agent,note,rev,text)).rowcount
                if changed != 1:
                    raise MigrationBlocked("concurrent_revision_change")
            if db.execute("""SELECT count(*) FROM scoped_agent_memory
                  WHERE text NOT LIKE ?""", ("aas:scoped:aesgcm:v1:%",)).fetchone()[0]:
                raise MigrationBlocked("migration_incomplete")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        # A post-commit VACUUM reduces reachable leftover plaintext pages but
        # cannot promise physical secure erasure (SSD wear-leveling/backups).
        db.execute("VACUUM")
    except (sqlite3.Error, OSError):
        raise MigrationBlocked("migration_storage_uncertain_inspect_offline") from None
    finally:
        db.close()
    # Reopen using the full, fail-closed production reader, not a test-only
    # decryption shortcut. Wrong key or row-tampering must block.
    reader = LocalScopedAgentMemory(path, encryption_key=master_key)
    try:
        scopes={(row[0],row[1]) for row in records}
        observed = sum(len(reader.list(p,a)) for p,a in scopes)
        if observed != len(records):
            raise MigrationBlocked("migration_verification_failed")
    finally:
        reader.close()
    return {"status":"ENCRYPTED_LIVE_NOTES_VERIFIED",
            "rows_migrated":len(records),
            "backup_erase_verified":False,
            "independent_backup_verified":False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline scoped memory migration (not a server).")
    parser.add_argument("--migrate",action="store_true",required=True)
    args = parser.parse_args(argv)
    # Do not race the standard local server listening at its fixed address.
    guard=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1",8765))
        guard.listen(1)
    except OSError:
        guard.close()
        raise SystemExit("Stop the private local Studio before migration.")
    try:
        home=Path.home()/".ai-automation-studio"
        owner=LocalOperatorAuth.load(home/"operator.json")
        password=getpass.getpass("Current owner password (never echoed): ")
        if owner.confirm_current_password(password)!=200:
            raise SystemExit("Owner authentication refused; migration did not start.")
        print("WARNING: previous plaintext DB copies, snapshots, journals and backups may persist.")
        print("Ensure an independently verified, encrypted offline backup and separate key recovery first.")
        approval=input("To migrate the live database, type exactly MIGRATE SCOPED MEMORY: ")
        if approval!="MIGRATE SCOPED MEMORY":
            raise SystemExit("Explicit approval was not given; database unchanged.")
        key=read_runtime_secret("AAS_SCOPED_MEMORY_KEY")
        audit=LocalSecurityAudit(home/"security-audit.sqlite3")
        try:
            if not audit.record("SCOPED_MEMORY_MIGRATION","ATTEMPT"):
                raise SystemExit("Security audit unavailable; migration refused.")
            try:
                result=migrate_encrypted_notes(
                    home/"scoped-agent-memory.sqlite3",key,acknowledged=True)
            except Exception:
                audit.record("SCOPED_MEMORY_MIGRATION","UNCERTAIN")
                raise
            audit.record("SCOPED_MEMORY_MIGRATION","SUCCESS")
            print("Encrypted live records verified:",result["rows_migrated"])
            print("Start Studio with --enable-encrypted-scoped-memory.")
            print("Historical/backup plaintext erasure is NOT proven.")
        finally:
            audit.close()
    finally:
        guard.close()


if __name__=="__main__":
    main()

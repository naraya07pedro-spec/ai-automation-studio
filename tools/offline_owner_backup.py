"""Offline encrypted owner-state backup, integrity check and create-only restore.

Owner Studio must be STOPPED for backup. No plaintext staging archive, network
calls, service restarts or replacement of an existing owner directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import sqlite3
import stat
import sys
import zipfile

MAGIC = b"AAS-OWNER-BACKUP-V1\x00"
AAD = b"AI-AUTOMATION-STUDIO/OWNER-STATE/V1"
MANIFEST = "__aas_manifest_v1__.json"
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_ARCHIVE = 134 * 1024 * 1024
MAX_FILES = 150
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")


class BackupError(RuntimeError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise BackupError("cryptography_optional_dependency_required") from None
    return AESGCM


def _derive(passphrase, salt):
    if (type(passphrase) is not str or not 16 <= len(passphrase) <= 256
            or len(passphrase.encode("utf-8")) > 512
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in passphrase)):
        raise BackupError("passphrase_requires_16_to_256_safe_characters")
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def _valid_rel(name):
    if type(name) is not str or not 1 <= len(name) <= 250:
        return False
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or name == MANIFEST:
        return False
    segments = name.split("/")
    return all(
        seg not in ("", ".", "..") and not seg.endswith((".", " "))
        and ":" not in seg and "\\" not in seg
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in seg)
        and seg.split(".")[0].upper() not in
        {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)),
         *(f"LPT{i}" for i in range(1,10))}
        for seg in segments
    )


def _check_private(path):
    if path.is_symlink():
        raise BackupError("symlink_forbidden")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BackupError("regular_file_required")
    if os.name == "posix" and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BackupError("source_file_permissions_unsafe")
    if info.st_size > MAX_FILE:
        raise BackupError("source_file_too_large")
    return info.st_size


def _source_root(root):
    if root.is_symlink() or not root.is_dir():
        raise BackupError("owner_root_missing_or_symlinked")
    if os.name == "posix":
        st = root.stat()
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise BackupError("owner_root_permissions_unsafe")
    if not (root / "operator.json").is_file():
        raise BackupError("operator_configuration_missing")


@contextmanager
def _offline_guard():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.bind(("127.0.0.1", 8765))
            sock.listen(1)
        except OSError:
            raise BackupError("stop_local_studio_before_backup") from None
        yield
    finally:
        sock.close()


def _sqlite_snapshot(path):
    # Refuse lingering journal sidecars rather than editing original SQLite.
    for suffix in SIDE_SUFFIXES:
        if Path(str(path) + suffix).exists():
            raise BackupError("sqlite_sidecar_requires_clean_shutdown")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro",
                                     uri=True, timeout=3)) as original:
            with closing(sqlite3.connect(":memory:")) as target:
                original.backup(target, pages=128, sleep=0.01)
                if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise BackupError("sqlite_quick_check_failed")
                result = target.serialize()
    except sqlite3.Error:
        raise BackupError("sqlite_snapshot_failed") from None
    if len(result) > MAX_FILE:
        raise BackupError("sqlite_snapshot_exceeds_limit")
    return result


def _check_sqlite_bytes(content):
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(content)
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise BackupError("restored_sqlite_integrity_failed")
    except sqlite3.Error:
        raise BackupError("restored_sqlite_integrity_failed") from None


def _snapshot(root):
    _source_root(root)
    result = {}
    total = 0
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise BackupError("symlink_in_owner_tree")
        if item.is_dir():
            continue
        name = item.relative_to(root).as_posix()
        if not _valid_rel(name):
            raise BackupError("unsupported_owner_state_path")
        if item.suffix == ".sqlite3" and any(
            (item.with_name(item.name + suffix)).exists()
            for suffix in SIDE_SUFFIXES
        ):
            raise BackupError("sqlite_sidecar_requires_clean_shutdown")
        if any(name.endswith(".sqlite3" + suffix) for suffix in SIDE_SUFFIXES):
            raise BackupError("sqlite_sidecar_requires_clean_shutdown")
        before = _check_private(item)
        value = _sqlite_snapshot(item) if item.suffix == ".sqlite3" else item.read_bytes()
        if len(value) > MAX_FILE:
            raise BackupError("source_file_too_large")
        # Non-SQLite files are checked for concurrent size changes, too.
        if item.suffix != ".sqlite3" and len(value) != before:
            raise BackupError("source_changed_during_backup")
        total += len(value)
        if total > MAX_TOTAL or len(result) >= MAX_FILES:
            raise BackupError("backup_size_or_file_count_exceeded")
        result[name] = value
    if "operator.json" not in result:
        raise BackupError("operator_configuration_missing")
    return result


def _pack(files):
    manifest = {"version": 1, "files": {
        n: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
        for n, v in sorted(files.items())
    }}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, sort_keys=True,
                                            separators=(",", ":")).encode())
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return stream.getvalue()


def _unpack(plain):
    try:
        with zipfile.ZipFile(io.BytesIO(plain), "r") as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (len(names) != len(set(names)) or names.count(MANIFEST) != 1
                    or len(names) > MAX_FILES + 1):
                raise BackupError("archive_entries_invalid")
            if any(item.file_size > MAX_FILE for item in entries
                   if item.filename != MANIFEST):
                raise BackupError("archive_size_limit_exceeded")
            if sum(item.file_size for item in entries) > MAX_ARCHIVE:
                raise BackupError("archive_size_limit_exceeded")
            record = json.loads(archive.read(MANIFEST))
            if (type(record) is not dict or set(record) != {"version", "files"}
                    or type(record["version"]) is not int or record["version"] != 1
                    or type(record["files"]) is not dict
                    or set(record["files"]) != set(names) - {MANIFEST}):
                raise BackupError("manifest_invalid")
            files = {}
            for name in sorted(record["files"]):
                spec = record["files"][name]
                if (not _valid_rel(name) or type(spec) is not dict
                        or set(spec) != {"bytes", "sha256"}
                        or type(spec["bytes"]) is not int
                        or not 0 <= spec["bytes"] <= MAX_FILE
                        or type(spec["sha256"]) is not str
                        or len(spec["sha256"]) != 64):
                    raise BackupError("manifest_invalid")
                content = archive.read(name)
                if (len(content) != spec["bytes"]
                        or hashlib.sha256(content).hexdigest() != spec["sha256"]):
                    raise BackupError("archive_digest_mismatch")
                if name.endswith(".sqlite3"):
                    _check_sqlite_bytes(content)
                files[name] = content
            if "operator.json" not in files:
                raise BackupError("operator_configuration_missing")
            return files
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError,
            OSError, RuntimeError) as error:
        if isinstance(error, BackupError):
            raise
        raise BackupError("invalid_or_corrupt_backup_archive") from None


def _encrypt(plain, password):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = _derive(password, salt)
    blob = MAGIC + salt + nonce + _crypto()(key).encrypt(nonce, plain, AAD)
    if len(blob) > MAX_ARCHIVE:
        raise BackupError("encrypted_archive_size_exceeded")
    return blob


def _decrypt(blob, password):
    if (len(blob) > MAX_ARCHIVE or len(blob) < len(MAGIC) + 16 + 12 + 16
            or not blob.startswith(MAGIC)):
        raise BackupError("backup_format_invalid")
    offset = len(MAGIC)
    salt, nonce = blob[offset:offset+16], blob[offset+16:offset+28]
    from cryptography.exceptions import InvalidTag
    try:
        return _crypto()(_derive(password, salt)).decrypt(
            nonce, blob[offset+28:], AAD)
    except (ValueError, InvalidTag):
        raise BackupError("backup_authentication_failed") from None


def _outside_important_roots(target, source):
    raw = target.expanduser().absolute()
    if any(part.is_symlink() for part in (raw, *raw.parents)):
        raise BackupError("symlinked_destination_forbidden")
    path = raw.resolve(strict=False)
    root = source.expanduser().resolve(strict=False)
    repository = Path(__file__).resolve().parents[1]
    if path == root or path.is_relative_to(root) or path.is_relative_to(repository):
        raise BackupError("destination_inside_live_owner_or_git_checkout")
    if not path.parent.is_dir():
        raise BackupError("destination_parent_must_exist")
    return path


def _write_new_file(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(path, 0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create(source, archive, password):
    with _offline_guard():
        files = _snapshot(source)
        sealed = _encrypt(_pack(files), password)
        path = _outside_important_roots(archive, source)
        _write_new_file(path, sealed)
    return {"status": "ENCRYPTED_OFFLINE_BACKUP_CREATED",
            "file_count": len(files), "total_plaintext_bytes": sum(map(len, files.values())),
            "encrypted_sha256": hashlib.sha256(sealed).hexdigest(),
            "live_owner_state_modified": False}


def inspect(archive, password):
    path = Path(archive).expanduser()
    _check_private(path)
    if path.stat().st_size > MAX_ARCHIVE:
        raise BackupError("archive_too_large")
    blob = path.read_bytes()
    files = _unpack(_decrypt(blob, password))
    return files


def verify(archive, password):
    files = inspect(archive, password)
    return {"status": "ENCRYPTED_BACKUP_AUTHENTICATED_AND_SQLITE_CHECKED",
            "file_count": len(files), "plaintext_restored_to_disk": False}


def restore_test(archive, dest, password, *, consent=False):
    if not consent:
        raise BackupError("explicit_restore_test_consent_required")
    source = Path.home() / ".ai-automation-studio"
    path = _outside_important_roots(Path(dest), source)
    if path.exists() or path.is_symlink():
        raise BackupError("restore_destination_must_not_exist")
    files = inspect(archive, password)
    path.mkdir(mode=0o700)
    if os.name == "posix":
        os.chmod(path, 0o700)
    # Only create new files in this already exclusive new tree.
    # On errors do not remove partial files (forensics / user review).
    for name, data in sorted(files.items()):
        relative = PurePosixPath(name).parts
        target = path.joinpath(*relative)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        _write_new_file(target, data)
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise BackupError("disk_restore_digest_mismatch")
        if name.endswith(".sqlite3"):
            _check_sqlite_bytes(target.read_bytes())
    return {"status": "DISPOSABLE_RESTORE_TO_NEW_DIRECTORY_CHECKED",
            "file_count": len(files),
            "plaintext_test_folder_requires_owner_cleanup": True,
            "live_owner_state_modified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline encrypted owner backup; no automatic live restore.")
    modes = parser.add_subparsers(dest="command", required=True)
    backup = modes.add_parser("create")
    backup.add_argument("--output", required=True)
    check = modes.add_parser("verify")
    check.add_argument("--archive", required=True)
    trial = modes.add_parser("restore-test")
    trial.add_argument("--archive", required=True)
    trial.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        password = getpass.getpass("Offline backup passphrase (not echoed): ")
        if args.command == "create":
            confirm = getpass.getpass("Confirm backup passphrase: ")
            if not secrets.compare_digest(password, confirm):
                raise BackupError("passphrase_confirmation_mismatch")
            result = create(Path.home() / ".ai-automation-studio",
                            Path(args.output), password)
        elif args.command == "verify":
            result = verify(Path(args.archive), password)
        else:
            answer = input("Type RESTORE_TEST_PLAINTEXT to create a separate "
                           "PLAINTEXT test folder: ").strip()
            result = restore_test(Path(args.archive), Path(args.destination),
                                  password, consent=answer == "RESTORE_TEST_PLAINTEXT")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BackupError, OSError, sqlite3.Error) as exc:
        print("OFFLINE_BACKUP_BLOCKED: " + (
            str(exc) if isinstance(exc, BackupError) else "filesystem_error"),
            file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

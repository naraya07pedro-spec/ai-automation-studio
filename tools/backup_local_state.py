"""Offline encrypted owner-state backup and create-only disposable recovery.

No VPS, network, model requests, active-state restore, or passphrases in argv.
The normal Studio listener MUST be stopped before backup. SQLite uses the
native backup API; WAL/shm/journal sidecars are never copied as raw files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import stat
import sys
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AAS-OWNER-BACKUP-V1\x00"
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_FILES = 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ENVELOPE_BYTES = 70 * 1024 * 1024
MANIFEST = "_aas_manifest.json"
SQLITE_EXTENSIONS = frozenset((".sqlite", ".sqlite3", ".db"))
SIDE_CARS = ("-wal", "-shm", "-journal")
KDF_WORK_FACTOR = 2**14
_BAD_WINDOWS_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED = frozenset(("CON", "PRN", "AUX", "NUL",
                       *(f"COM{i}" for i in range(1, 10)),
                       *(f"LPT{i}" for i in range(1, 10))))


class BackupError(RuntimeError):
    pass


def _valid_name(name: str) -> bool:
    if not isinstance(name, str) or not name or len(name) > 1024:
        return False
    if name == MANIFEST or name.startswith("/") or "\\" in name:
        return False
    parts = PurePosixPath(name).parts
    if not parts or "/".join(parts) != name or len(parts) > 16:
        return False
    return all(
        part not in ("", ".", "..") and len(part) <= 200
        and not part.endswith((" ", "."))
        and not _BAD_WINDOWS_CHARS.search(part)
        and part.split(".")[0].upper() not in _RESERVED
        for part in parts
    )


def _is_sidecar(name: str) -> bool:
    lower = name.lower()
    for suffix in SIDE_CARS:
        if lower.endswith(suffix):
            return Path(lower[:-len(suffix)]).suffix in SQLITE_EXTENSIONS
    return False


def _file_kind(name: str) -> str:
    return "sqlite" if PurePosixPath(name).suffix.lower() in SQLITE_EXTENSIONS else "file"


def _check_source(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("owner_state_missing_or_symlink")
    if root.parent.is_symlink():
        raise BackupError("symlink_owner_parent_refused")
    if os.name == "posix":
        info = root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupError("owner_state_permissions_unsafe")


def _inventory(root: Path) -> list[tuple[str, Path]]:
    items = []
    names = set()
    for child in sorted(root.rglob("*")):
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise BackupError("symlink_or_junction_in_owner_state")
        name = child.relative_to(root).as_posix()
        if not _valid_name(name):
            raise BackupError("unsupported_state_filename")
        if child.is_dir():
            continue
        if not child.is_file():
            raise BackupError("nonregular_state_entry")
        if _is_sidecar(name):
            continue
        if name.casefold() in names:
            raise BackupError("case_collision_in_state")
        names.add(name.casefold())
        info = child.stat()
        if info.st_size > MAX_FILE_BYTES:
            raise BackupError("state_file_exceeds_limit")
        if os.name == "posix" and (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise BackupError("state_file_permissions_unsafe")
        items.append((name, child))
        if len(items) > MAX_FILES:
            raise BackupError("too_many_state_files")
    return items


def _studio_stopped() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 8765))
        return True
    except OSError:
        return False


def _check_sqlite(blob: bytes) -> None:
    if not blob.startswith(b"SQLite format 3\x00"):
        raise BackupError("invalid_sqlite_header")
    try:
        with sqlite3.connect(":memory:") as db:
            db.deserialize(blob)
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise BackupError("sqlite_integrity_failed")
    except (sqlite3.Error, ValueError, OverflowError) as exc:
        raise BackupError("sqlite_integrity_failed") from exc


def _snapshot_sqlite(path: Path) -> bytes:
    try:
        # Native SQLite online backup copies committed data and pending WAL.
        source_uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=5) as source:
            with sqlite3.connect(":memory:") as target:
                source.backup(target, pages=128, sleep=0.05)
                blob = target.serialize()
        if len(blob) > MAX_FILE_BYTES:
            raise BackupError("sqlite_snapshot_exceeds_limit")
        _check_sqlite(blob)
        return blob
    except (sqlite3.Error, ValueError, OverflowError) as exc:
        raise BackupError("sqlite_snapshot_failed") from exc


def _read_state_file(path: Path, kind: str) -> bytes:
    before = path.stat()
    if kind == "sqlite":
        blob = _snapshot_sqlite(path)
    else:
        with path.open("rb") as stream:
            blob = stream.read(MAX_FILE_BYTES + 1)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise BackupError("owner_file_changed_while_backing_up")
    if len(blob) > MAX_FILE_BYTES:
        raise BackupError("state_file_exceeds_limit")
    return blob


def _password_ok(password: str) -> bool:
    return (isinstance(password, str)
            and 16 <= len(password) <= 256
            and len(password.encode("utf-8")) <= 512
            and not any(ord(c) < 32 or ord(c) == 127 for c in password))


def _key(password: str, salt: bytes) -> bytes:
    if not _password_ok(password):
        raise BackupError("backup_passphrase_policy_failed")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=KDF_WORK_FACTOR, r=8, p=1, dklen=32,
                          maxmem=128 * 1024 * 1024)


def _encrypt(raw: bytes, password: str) -> bytes:
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = MAGIC + salt + nonce
    return header + AESGCM(_key(password, salt)).encrypt(nonce, raw, header)


def _decrypt(envelope: bytes, password: str) -> bytes:
    if not envelope.startswith(MAGIC) or len(envelope) <= len(MAGIC) + 44:
        raise BackupError("invalid_backup_envelope")
    start = len(MAGIC)
    salt = envelope[start:start + SALT_BYTES]
    nonce = envelope[start + SALT_BYTES:start + SALT_BYTES + NONCE_BYTES]
    header = envelope[:start + SALT_BYTES + NONCE_BYTES]
    try:
        return AESGCM(_key(password, salt)).decrypt(
            nonce, envelope[len(header):], header
        )
    except InvalidTag as exc:
        raise BackupError("backup_authentication_failed") from exc


def _checked_parent(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise BackupError("destination_must_be_absolute")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise BackupError("destination_parent_invalid")
    if os.name == "posix":
        info = parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupError("destination_parent_permissions_unsafe")


def _exclusive_file(path: Path, data: bytes) -> None:
    _checked_parent(path)
    if path.exists() or path.is_symlink():
        raise BackupError("destination_exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
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


def _assemble(root: Path) -> bytes:
    inventory = _inventory(root)
    if not inventory:
        raise BackupError("empty_owner_state")
    data = {}
    entries = []
    total = 0
    for name, path in inventory:
        kind = _file_kind(name)
        blob = _read_state_file(path, kind)
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise BackupError("total_state_exceeds_limit")
        data[name] = blob
        entries.append({"path": name, "type": kind, "size": len(blob),
                        "sha256": hashlib.sha256(blob).hexdigest()})
    # Detect added/removed files during the offline snapshot.
    if [name for name, _ in _inventory(root)] != [name for name, _ in inventory]:
        raise BackupError("owner_inventory_changed_while_backing_up")
    manifest = {
        "version": 1,
        "kind": "offline-owner-state",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, sort_keys=True))
        for name, blob in data.items():
            zf.writestr(name, blob)
    return out.getvalue()


def create_backup(root: Path, destination: Path, password: str) -> dict:
    root = Path(root).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    _check_source(root)
    if not _studio_stopped():
        raise BackupError("stop_studio_before_backup")
    if root == destination or root in destination.parents:
        raise BackupError("backup_must_be_outside_owner_state")
    _checked_parent(destination)
    if destination.exists() or destination.is_symlink():
        raise BackupError("destination_exists")
    payload = _assemble(root)
    envelope = _encrypt(payload, password)
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise BackupError("encrypted_backup_exceeds_limit")
    _exclusive_file(destination, envelope)
    return {"status": "ENCRYPTED_OWNER_BACKUP_CREATED",
            "encrypted_bytes": len(envelope),
            "live_server_stopped_at_capture": True,
            "cloud_or_provider_verified": False}


def _open_verified(archive: Path, password: str) -> tuple[dict, dict[str, bytes]]:
    archive = Path(archive).expanduser().absolute()
    if archive.is_symlink() or not archive.is_file():
        raise BackupError("archive_missing_or_symlink")
    size = archive.stat().st_size
    if size > MAX_ENVELOPE_BYTES:
        raise BackupError("archive_exceeds_limit")
    encrypted = archive.read_bytes()
    plaintext = _decrypt(encrypted, password)
    files = {}
    try:
        with zipfile.ZipFile(BytesIO(plaintext)) as zf:
            members = zf.infolist()
            if not members or len(members) > MAX_FILES + 1:
                raise BackupError("invalid_archive_members")
            if len({m.filename.casefold() for m in members}) != len(members):
                raise BackupError("archive_name_collision")
            if any(m.is_dir() or
                   (m.filename != MANIFEST and not _valid_name(m.filename))
                   for m in members):
                raise BackupError("unsafe_archive_paths")
            names = {m.filename for m in members}
            if MANIFEST not in names:
                raise BackupError("manifest_missing")
            if sum(m.file_size for m in members) > MAX_TOTAL_BYTES + 262144:
                raise BackupError("archive_decompression_limit")
            for m in members:
                if m.file_size > MAX_FILE_BYTES and m.filename != MANIFEST:
                    raise BackupError("archive_member_exceeds_limit")
            mi = zf.getinfo(MANIFEST)
            if mi.file_size > 262144:
                raise BackupError("manifest_exceeds_limit")
            manifest = json.loads(zf.read(MANIFEST))
            if (not isinstance(manifest, dict)
                    or set(manifest) != {"version", "kind", "captured_utc", "entries"}
                    or manifest["version"] != 1
                    or manifest["kind"] != "offline-owner-state"
                    or not isinstance(manifest["entries"], list)
                    or len(manifest["entries"]) != len(members) - 1):
                raise BackupError("invalid_manifest")
            expected = set()
            for e in manifest["entries"]:
                if (not isinstance(e, dict)
                        or set(e) != {"path", "type", "size", "sha256"}
                        or not _valid_name(e["path"])
                        or e["path"] in expected
                        or e["type"] != _file_kind(e["path"])
                        or type(e["size"]) is not int
                        or e["size"] < 0 or e["size"] > MAX_FILE_BYTES
                        or not isinstance(e["sha256"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", e["sha256"])):
                    raise BackupError("invalid_manifest_entry")
                expected.add(e["path"])
                blob = zf.read(e["path"])
                if (len(blob) != e["size"]
                        or hashlib.sha256(blob).hexdigest() != e["sha256"]):
                    raise BackupError("archive_digest_mismatch")
                if e["type"] == "sqlite":
                    _check_sqlite(blob)
                files[e["path"]] = blob
            if expected != names - {MANIFEST}:
                raise BackupError("archive_manifest_mismatch")
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
            RuntimeError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("archive_invalid_or_corrupt") from exc
    return manifest, files


def verify_backup(archive: Path, password: str) -> dict:
    manifest, files = _open_verified(archive, password)
    return {
        "status": "ENCRYPTED_BACKUP_INTEGRITY_VERIFIED",
        "file_count": len(files),
        "sqlite_count": sum(e["type"] == "sqlite" for e in manifest["entries"]),
        "can_restore_active_owner_state": False,
    }


def restore_disposable(archive: Path, destination: Path, password: str,
                       *, active_root: Path | None = None) -> dict:
    destination = Path(destination).expanduser().absolute()
    root = (Path(active_root).expanduser().absolute() if active_root is not None
            else Path.home() / ".ai-automation-studio")
    if destination == root or root in destination.parents:
        raise BackupError("active_owner_state_restore_forbidden")
    _checked_parent(destination)
    if destination.exists() or destination.is_symlink():
        raise BackupError("restore_destination_exists")
    manifest, files = _open_verified(archive, password)  # No writes until valid.
    destination.mkdir(mode=0o700)
    try:
        for item in manifest["entries"]:
            name = item["path"]
            target = destination.joinpath(*PurePosixPath(name).parts)
            rel_parent = target.parent.relative_to(destination)
            current = destination
            for component in rel_parent.parts:
                current = current / component
                if not current.exists():
                    current.mkdir(mode=0o700)
                if current.is_symlink() or not current.is_dir():
                    raise BackupError("restore_parent_unsafe")
            _exclusive_file(target, files[name])
    except BaseException:
        # Never erase partial evidence or an unexpectedly replaced path.
        raise BackupError("disposable_restore_incomplete_review_directory")
    return {"status": "DISPOSABLE_RESTORE_VERIFIED",
            "restored_files": len(files),
            "owner_state_modified": False,
            "no_active_state_overwrite": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline encrypted local state backup.")
    parser.add_argument("action", choices=("backup", "verify", "restore-disposable"))
    parser.add_argument("--archive", required=True,
                        help="Encrypted archive path; secrets are never CLI arguments.")
    parser.add_argument("--destination",
                        help="New, nonexistent disposable restore directory.")
    args = parser.parse_args(argv)
    try:
        if args.action == "restore-disposable" and not args.destination:
            raise BackupError("disposable_destination_required")
        password = getpass.getpass("Private backup passphrase: ")
        if args.action == "backup":
            confirmation = getpass.getpass("Confirm backup passphrase: ")
            if password != confirmation:
                raise BackupError("backup_passphrase_confirmation_mismatch")
            result = create_backup(
                Path.home() / ".ai-automation-studio", Path(args.archive), password
            )
        elif args.action == "verify":
            result = verify_backup(Path(args.archive), password)
        else:
            result = restore_disposable(
                Path(args.archive), Path(args.destination), password
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BackupError, OSError) as exc:
        # No password, path, prompt, filenames or decrypted content in errors.
        code = str(exc) if isinstance(exc, BackupError) else "filesystem_error"
        print(json.dumps({"status": "BLOCKED", "reason": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

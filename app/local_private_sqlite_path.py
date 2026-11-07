"""Small single-owner private SQLite path guard for existing local ledgers.

No arbitrary path access over HTTP. POSIX regular files must be single-link and
0600, private parent directories 0700; create without a permissive first-open
window. Windows relies on inherited ACL and separate owner ACL diagnostics.
"""
import os
from pathlib import Path
import stat


class PrivateSQLitePathError(RuntimeError):
    pass


def prepare_private_sqlite(database):
    if str(database)==":memory:":
        return ":memory:"
    path=Path(database).expanduser()
    if path.is_symlink() or path.parent.is_symlink():
        raise PrivateSQLitePathError("private_sqlite_path_symlink_forbidden")
    created_parent=not path.parent.exists()
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PrivateSQLitePathError("private_sqlite_parent_unsafe")
    if os.name=="posix":
        if created_parent:
            os.chmod(path.parent,0o700)
        info=path.parent.stat()
        if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise PrivateSQLitePathError("private_sqlite_parent_permissions_unsafe")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PrivateSQLitePathError("private_sqlite_file_unsafe")
        if os.name=="posix":
            info=path.stat()
            if (info.st_uid!=os.geteuid() or info.st_nlink!=1):
                raise PrivateSQLitePathError("private_sqlite_file_owner_unsafe")
            # Historical SQLite created with process umask (often 0644).
            # Narrow old owner-owned files in place, without truncating data.
            if stat.S_IMODE(info.st_mode)&0o077:
                os.chmod(path,0o600)
    else:
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)
        try:
            fd=os.open(str(path),flags,0o600)
        except FileExistsError:
            # Races are not automatically trusted as an existing file.
            raise PrivateSQLitePathError("private_sqlite_file_creation_race") from None
        finally:
            pass
        os.close(fd)
    return str(path)

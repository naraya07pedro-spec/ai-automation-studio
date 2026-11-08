"""Stage 123: operator-driven, sealed two-copy recovery rehearsal.

Runs the existing Stage 111 cryptographic backup/verify/restore and optional
Stage 118 six-ledger semantic auditor as one fail-closed, bounded workflow.
Never claims that a second path is physically offsite or on separate hardware.
No network, provider credentials, unapproved writes, unattended scheduling,
production restore or silent plaintext staging.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from offline_owner_backup import (
    BackupError, MAX_ARCHIVE, _check_private, _outside_important_roots,
    _source_root, create, verify, restore_test,
)
from owner_acceptance_audit import AcceptanceError, audit, PLAN_ID

CONFIRM_PLAINTEXT = "RESTORE_TEST_PLAINTEXT"
COPY_CHUNK = 1024 * 1024


class RecoveryDrillError(RuntimeError):
    pass


def _sealed_digest(path):
    """Hash an existing regular private sealed file, without reading plaintext."""
    _check_private(path)
    if path.stat().st_size > MAX_ARCHIVE:
        raise RecoveryDrillError("encrypted_archive_size_limit")
    sha = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            part = stream.read(COPY_CHUNK)
            if not part:
                break
            total += len(part)
            if total > MAX_ARCHIVE:
                raise RecoveryDrillError("encrypted_archive_size_limit")
            sha.update(part)
    return sha.hexdigest(), total


def _sealed_copy_once(source, destination, *, expected_sha):
    """Copy ciphertext create-only; never read/decrypt raw owner state."""
    if destination.exists() or destination.is_symlink():
        raise RecoveryDrillError("secondary_copy_must_not_exist")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(destination), flags, 0o600)
    except FileExistsError:
        raise RecoveryDrillError("secondary_copy_must_not_exist") from None
    except OSError:
        raise RecoveryDrillError("secondary_copy_creation_failed") from None
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
            while True:
                chunk = input_stream.read(COPY_CHUNK)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "posix":
            os.chmod(destination, 0o600)
        observed, _ = _sealed_digest(destination)
        if not secrets.compare_digest(observed, expected_sha):
            raise RecoveryDrillError("secondary_copy_hash_mismatch")
    except BaseException:
        # The only artifact eligible for rollback is the secondary copy
        # freshly created by this operation. Primary archive is untouched.
        destination.unlink(missing_ok=True)
        raise


def _preflight(owner, primary, secondary, restored, plan_id, consent):
    owner = Path(owner).expanduser()
    _source_root(owner)
    primary = _outside_important_roots(Path(primary), owner)
    secondary = _outside_important_roots(Path(secondary), owner)
    if primary == secondary:
        raise RecoveryDrillError("primary_and_secondary_must_differ")
    if primary.exists() or primary.is_symlink():
        raise RecoveryDrillError("primary_archive_must_not_exist")
    if secondary.exists() or secondary.is_symlink():
        raise RecoveryDrillError("secondary_copy_must_not_exist")
    if plan_id is not None and (type(plan_id) is not str or
                                PLAN_ID.fullmatch(plan_id) is None):
        raise RecoveryDrillError("invalid_plan_for_semantic_audit")
    if plan_id is not None and restored is None:
        raise RecoveryDrillError("semantic_audit_requires_disposable_restore")
    if restored is not None:
        if consent is not True:
            raise RecoveryDrillError("explicit_plaintext_restore_consent_required")
        restored = _outside_important_roots(Path(restored), owner)
        if restored == primary or restored == secondary:
            raise RecoveryDrillError("restore_directory_collides_with_archive")
        if (restored == primary.parent or restored == secondary.parent
                or primary.is_relative_to(restored)
                or secondary.is_relative_to(restored)):
            raise RecoveryDrillError("restore_must_not_contain_sealed_archives")
        if restored.exists() or restored.is_symlink():
            raise RecoveryDrillError("restore_directory_must_not_exist")
    return owner, primary, secondary, restored


def drill(owner_root, primary_archive, secondary_archive, password, *,
          restore_directory=None, plaintext_consent=False, plan_id=None,
          expected_host=None, expected_workflow=None, expected_node=None,
          require_gmail_sent_chain=False, expected_gmail_node=None):
    """One coordinated owner-initiated recovery rehearsal.

    Any failure is terminal and leaves already created encrypted archives
    intact for private owner investigation. A deliberately requested plaintext
    disposable restore remains for manual owner cleanup, never auto-erased.
    """
    owner, primary, secondary, restored = _preflight(
        owner_root, primary_archive, secondary_archive, restore_directory,
        plan_id, plaintext_consent)
    if any(x is not None for x in (
            expected_host,expected_workflow,expected_node,expected_gmail_node)
            ) and plan_id is None:
        raise RecoveryDrillError("provider_pins_require_semantic_audit_plan")
    if require_gmail_sent_chain and (
            plan_id is None or
            any(v is None for v in (
                expected_host,expected_workflow,expected_node,expected_gmail_node))):
        raise RecoveryDrillError("strict_gmail_audit_requires_plan_and_four_pins")
    if not require_gmail_sent_chain and expected_gmail_node is not None:
        raise RecoveryDrillError("gmail_node_pin_requires_strict_audit")
    original = create(owner, primary, password)
    first_check = verify(primary, password)
    first_hash, first_size = _sealed_digest(primary)
    if first_hash != original["encrypted_sha256"]:
        raise RecoveryDrillError("primary_changed_after_backup")
    _sealed_copy_once(primary, secondary, expected_sha=first_hash)
    second_hash, second_size = _sealed_digest(secondary)
    if (not secrets.compare_digest(first_hash, second_hash)
            or first_size != second_size):
        raise RecoveryDrillError("encrypted_copies_not_identical")
    second_check = verify(secondary, password)
    if (first_check["file_count"] != second_check["file_count"]
            or first_check["file_count"] != original["file_count"]):
        raise RecoveryDrillError("recovered_file_inventory_mismatch")
    # os.stat().st_dev is a filesystem identity hint, NOT proof that
    # independent physical/offsite storage really exists.
    distinct_filesystem = primary.stat().st_dev != secondary.stat().st_dev
    result = {
        "status":"SEALED_TWO_COPY_RECOVERY_DRILL_VERIFIED",
        "stage":"123",
        "primary_archive_authenticated":True,
        "secondary_archive_authenticated":True,
        "copies_byte_identical":True,
        "sealed_sha256":first_hash,
        "sealed_bytes":first_size,
        "authenticated_archive_file_count":first_check["file_count"],
        "separate_filesystem_reported":distinct_filesystem,
        "physical_off_device_copy_independently_verified":False,
        "plaintext_restored":False,
        "semantic_six_ledger_audit_passed":False,
        "semantic_audit_performed":False,
        "live_owner_environment_independently_verified":False,
        "external_credentials_recovered":False,
        "provider_business_effect_verified":False,
        "live_owner_state_modified":False,
        "new_remote_requests":0,
        "unattended_backup_enabled":False,
    }
    if restored is not None:
        recovery = restore_test(secondary, restored, password, consent=True)
        if recovery["file_count"] != first_check["file_count"]:
            raise RecoveryDrillError("disposable_restore_file_count_mismatch")
        result["plaintext_restored"]=True
        result["disposable_plaintext_folder_requires_owner_cleanup"]=True
        result["status"]="DISPOSABLE_TWO_COPY_RESTORE_CHECKED"
        if plan_id is not None:
            report = audit(restored, plan_id, expected_host=expected_host,
                           expected_workflow=expected_workflow,
                           expected_node=expected_node,
                           require_gmail_sent_chain=require_gmail_sent_chain,
                           expected_gmail_node=expected_gmail_node)
            expected_status = ("LOCAL_GMAIL_EVIDENCE_CHAIN_CONSISTENT"
                               if require_gmail_sent_chain else
                               "LOCAL_EVIDENCE_CHAIN_CONSISTENT")
            if report["status"] != expected_status:
                raise RecoveryDrillError("restored_evidence_chain_not_proven")
            result["semantic_audit_performed"]=True
            result["semantic_six_ledger_audit_passed"]=True
            result["status"]=("RESTORED_NINE_LAYER_GMAIL_EVIDENCE_CONSISTENT"
                              if require_gmail_sent_chain else
                              "RESTORED_SIX_LEDGER_CHAIN_CONSISTENT")
            result["local_ledger_layers_checked"]=len(report["checked_layers"])
            result["strict_saved_gmail_chain_audited"]=require_gmail_sent_chain
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=(
        "Explicit owner-run encrypted backup + independently opened ciphertext copy "
        "verification. Optional disposable plaintext restore and six-ledger audit."))
    p.add_argument("--primary",required=True,
                   help="New encrypted archive path outside Studio and git checkout")
    p.add_argument("--secondary",required=True,
                   help="Second NEW archive path, preferably separate offline drive")
    p.add_argument("--restore-directory",
                   help="Optional NEW disposable plaintext folder; explicit consent required")
    p.add_argument("--plan-id",help="Optional exact 24-hex Stage 114-117 completed plan")
    p.add_argument("--expected-n8n-host")
    p.add_argument("--expected-n8n-workflow")
    p.add_argument("--expected-n8n-node")
    p.add_argument("--require-gmail-sent-chain",action="store_true",
                   help="Require Stage 120-122 evidence on restored owner data")
    p.add_argument("--expected-n8n-gmail-node")
    args=p.parse_args(argv)
    try:
        consent=False
        if args.restore_directory is not None:
            consent=(input("Type "+CONFIRM_PLAINTEXT+" for a separate PLAINTEXT "
                           "disposable restore: ").strip()==CONFIRM_PLAINTEXT)
        # Reject mismatched configuration before asking for a secret.
        _preflight(Path.home()/".ai-automation-studio",args.primary,
                   args.secondary,args.restore_directory,args.plan_id,consent)
        passphrase=getpass.getpass("Offline encryption passphrase (hidden): ")
        repeat=getpass.getpass("Confirm passphrase (hidden): ")
        if not secrets.compare_digest(passphrase,repeat):
            raise RecoveryDrillError("passphrase_confirmation_mismatch")
        result=drill(Path.home()/".ai-automation-studio",args.primary,
                     args.secondary,passphrase,
                     restore_directory=args.restore_directory,
                     plaintext_consent=consent,plan_id=args.plan_id,
                     expected_host=args.expected_n8n_host,
                     expected_workflow=args.expected_n8n_workflow,
                     expected_node=args.expected_n8n_node,
                     require_gmail_sent_chain=args.require_gmail_sent_chain,
                     expected_gmail_node=args.expected_n8n_gmail_node)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (BackupError,RecoveryDrillError,AcceptanceError,OSError) as exc:
        reason=(str(exc) if isinstance(exc,(BackupError,RecoveryDrillError,AcceptanceError))
                else "private_filesystem_operation_failed")
        print(json.dumps({"status":"RECOVERY_DRILL_NOT_PROVEN",
                          "reason":reason,"owner_data_uploaded":False,
                          "provider_business_effect_verified":False,
                          "live_owner_state_modified":False,
                          "new_remote_requests":0},sort_keys=True))
        return 2


if __name__=="__main__":
    sys.exit(main())

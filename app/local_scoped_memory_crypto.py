"""Opt-in AES-256-GCM envelope for project/Agent-scoped owner memory text.

This encrypts ONLY the note text, not SQLite metadata, other Agent memory,
historical backup/swap files, planner prompts or in-memory model contexts.
Every ciphertext binds the immutable scope and its current CAS/provenance
metadata as AAD. Never infer a key from an owner password or auto-migrate.
"""
import base64
import binascii
import json
import os

from local_connector_vault import decode_master_key, VaultError

PREFIX = "aas:scoped:aesgcm:v1:"
DOMAIN = b"AAS:PROJECT-AGENT-SCOPED-MEMORY:V1\x00"
MAX_CIPHERTEXT_BYTES = 4096


class ScopedMemoryCryptoError(RuntimeError):
    pass


def _aad(project_id, agent_id, note_id, revision, generation,
         source_kind, source_label, expires_utc):
    return DOMAIN + json.dumps({
        "project": project_id, "agent": agent_id, "note": note_id,
        "revision": revision, "generation": generation,
        "source_kind": source_kind, "source_label": source_label,
        "expires_utc": expires_utc,
    }, sort_keys=True, ensure_ascii=True,
        separators=(",", ":")).encode("ascii")


class ScopedMemoryCipher:
    """Key must be an explicit independently-provisioned base64url 32-byte secret."""

    def __init__(self, master_key):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.exceptions import InvalidTag
        except ImportError:
            raise ScopedMemoryCryptoError(
                "scoped_memory_crypto_dependency_missing") from None
        try:
            key = decode_master_key(master_key)
        except VaultError:
            raise ScopedMemoryCryptoError(
                "scoped_memory_invalid_32_byte_key") from None
        self._aead = AESGCM(key)
        self._invalid_tag = InvalidTag

    @staticmethod
    def is_sealed(value):
        return type(value) is str and value.startswith(PREFIX)

    def seal(self, value, project_id, agent_id, note_id, revision, generation,
             source_kind, source_label, expires_utc):
        if type(value) is not str or not value or len(value) > 600:
            raise ScopedMemoryCryptoError("scoped_memory_invalid_plaintext")
        nonce = os.urandom(12)
        raw = value.encode("utf-8")
        encrypted = self._aead.encrypt(
            nonce, raw, _aad(project_id, agent_id, note_id, revision,
                             generation, source_kind, source_label, expires_utc))
        return PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def unseal(self, envelope, project_id, agent_id, note_id, revision,
               generation, source_kind, source_label, expires_utc):
        if (not self.is_sealed(envelope) or
                len(envelope) > MAX_CIPHERTEXT_BYTES):
            raise ScopedMemoryCryptoError("scoped_memory_unlock_failed")
        try:
            raw = base64.b64decode(
                envelope[len(PREFIX):], altchars=b"-_", validate=True)
            if len(raw) < 29 or len(raw) > MAX_CIPHERTEXT_BYTES:
                raise ValueError
            answer = self._aead.decrypt(
                raw[:12], raw[12:],
                _aad(project_id, agent_id, note_id, revision,
                     generation, source_kind, source_label, expires_utc))
            return answer.decode("utf-8", errors="strict")
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise ScopedMemoryCryptoError("scoped_memory_unlock_failed") from None
        except self._invalid_tag:
            raise ScopedMemoryCryptoError("scoped_memory_unlock_failed") from None

"""Field-level encryption for personal data, and keyed fingerprints for matching it.

A sealed value is `version byte + 12 byte nonce + AES-256-GCM ciphertext`. The caller passes
associated data naming where the value lives (`associated_data(table, column, row_id)`), so a
ciphertext copied to another row or column fails to open. A fingerprint is an HMAC, so equal
values can be matched, for a uniqueness rule, without storing or revealing them.

The key is given to the constructor, never read from settings here, and each purpose derives its
own encryption and fingerprint keys from it. Losing the key loses the data: it is generated per
environment, kept in the secrets store and backed up.
"""

import base64
import binascii
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_BYTES = 32
_NONCE_BYTES = 12
_VERSION = b"\x01"


class SealError(Exception):
    """A sealed value could not be opened: wrong key, wrong place, or it was altered."""


def key_from_text(value: str) -> bytes:
    """Decode a key as it is held in the environment: 32 random bytes, base64url."""
    try:
        key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("A sealing key is 32 bytes in base64url") from None
    if len(key) != KEY_BYTES:
        raise ValueError("A sealing key is 32 bytes in base64url")
    return key


def associated_data(table: str, column: str, row_id: str) -> bytes:
    return f"{table}:{column}:{row_id}".encode()


class Sealer:
    def __init__(self, key: bytes, *, purpose: str) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("A sealing key is 32 bytes")
        if not purpose:
            raise ValueError("A sealer needs a purpose")
        self._aead = AESGCM(self._derive(key, f"finvest:{purpose}:seal:v1"))
        self._fingerprint_key = self._derive(key, f"finvest:{purpose}:fingerprint:v1")

    @staticmethod
    def _derive(key: bytes, info: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=info.encode()
        ).derive(key)

    def seal(self, plaintext: str, associated: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), associated)

    def open(self, sealed: bytes, associated: bytes) -> str:
        sealed = bytes(sealed)
        if len(sealed) <= 1 + _NONCE_BYTES or sealed[:1] != _VERSION:
            raise SealError("Unknown sealed value format")
        nonce, ciphertext = sealed[1 : 1 + _NONCE_BYTES], sealed[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, ciphertext, associated).decode()
        except (InvalidTag, UnicodeDecodeError):
            # Never chain the cause: nothing about the value or key belongs in a traceback.
            raise SealError("The sealed value could not be opened") from None

    def fingerprint(self, value: str) -> str:
        """A stable 64 character digest of `value` that reveals nothing without the key."""
        return hmac.new(self._fingerprint_key, value.encode(), hashlib.sha256).hexdigest()

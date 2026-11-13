"""Field-level encryption for personal data, and keyed fingerprints for matching it.

A sealed value is `version byte + key id byte + 12 byte nonce + AES-256-GCM ciphertext`. The
key id says which key sealed it, so a key can be replaced: new values are sealed with the current
key while values sealed with a retired key still open, until they are sealed again.

The caller passes associated data naming where the value lives
(`associated_data(table, column, row_id)`), so a ciphertext copied to another row or column fails
to open. A fingerprint is an HMAC, so equal values can be matched, for a uniqueness rule, without
storing or revealing them.

The key is given to the constructor, never read from settings here, and each purpose derives its
own encryption and fingerprint keys from it. Losing the key loses the data: it is generated per
environment, kept in the secrets store and backed up.

Fingerprints are made with the current key only. Replacing the key therefore also means
computing them again, which nothing here does yet.
"""

import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_BYTES = 32
_NONCE_BYTES = 12
_VERSION = b"\x01"
_HEADER_BYTES = 2 + _NONCE_BYTES


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
    def __init__(
        self,
        key: bytes,
        *,
        purpose: str,
        key_id: int = 1,
        retired: Mapping[int, bytes] | None = None,
    ) -> None:
        if not purpose:
            raise ValueError("A sealer needs a purpose")
        keys = {**(retired or {}), key_id: key}
        if len(keys) != len(retired or {}) + 1:
            raise ValueError("A retired key cannot share the current key's id")
        for held_id, held in keys.items():
            if not 1 <= held_id <= 255:
                raise ValueError("A key id is a number from 1 to 255")
            if len(held) != KEY_BYTES:
                raise ValueError("A sealing key is 32 bytes")
        self._key_id = bytes([key_id])
        self._aeads = {
            bytes([held_id]): AESGCM(self._derive(held, f"finvest:{purpose}:seal:v1"))
            for held_id, held in keys.items()
        }
        self._fingerprint_key = self._derive(key, f"finvest:{purpose}:fingerprint:v1")

    @staticmethod
    def _derive(key: bytes, info: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=info.encode()
        ).derive(key)

    def seal(self, plaintext: str, associated: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aeads[self._key_id].encrypt(nonce, plaintext.encode(), associated)
        return _VERSION + self._key_id + nonce + sealed

    def open(self, sealed: bytes, associated: bytes) -> str:
        sealed = bytes(sealed)
        aead = self._aeads.get(sealed[1:2])
        if len(sealed) <= _HEADER_BYTES or sealed[:1] != _VERSION or aead is None:
            raise SealError("Unknown sealed value format or key")
        nonce, ciphertext = sealed[2:_HEADER_BYTES], sealed[_HEADER_BYTES:]
        try:
            return aead.decrypt(nonce, ciphertext, associated).decode()
        except (InvalidTag, UnicodeDecodeError):
            # Never chain the cause: nothing about the value or key belongs in a traceback.
            raise SealError("The sealed value could not be opened") from None

    def sealed_with_current_key(self, sealed: bytes) -> bool:
        """False for a value an earlier key sealed, which should be sealed again."""
        return bytes(sealed)[1:2] == self._key_id

    def fingerprint(self, value: str) -> str:
        """A stable 64 character digest of `value` that reveals nothing without the key."""
        return hmac.new(self._fingerprint_key, value.encode(), hashlib.sha256).hexdigest()

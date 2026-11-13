"""A software passkey for tests: what a phone or security key does, in a few lines.

It holds an ES256 key, answers `navigator.credentials.create` and `.get` the way a browser
would, and can be told to misbehave (wrong site, wrong origin, no user verification).
"""

import base64
import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

USER_PRESENT = 0x01
USER_VERIFIED = 0x04
ATTESTED = 0x40


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class Device:
    def __init__(self, site: str, origin: str) -> None:
        self.site = site
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.user_verified = True

    def _client_data(self, kind: str, options: dict[str, Any]) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": options["challenge"],
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def _authenticator_data(self, flags: int, extra: bytes = b"") -> bytes:
        if self.user_verified:
            flags |= USER_VERIFIED
        self.sign_count += 1
        return (
            hashlib.sha256(self.site.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
            + extra
        )

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        """Answer registration options with a new credential, attestation format `none`."""
        numbers = self.key.public_key().public_numbers()
        public_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + public_key
        )
        authenticator_data = self._authenticator_data(USER_PRESENT | ATTESTED, attested)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": authenticator_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options)),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
        }

    def get(self, options: dict[str, Any]) -> dict[str, Any]:
        """Answer sign-in options by signing the challenge."""
        client_data = self._client_data("webauthn.get", options)
        authenticator_data = self._authenticator_data(USER_PRESENT)
        signature = self.key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(signature),
            },
        }

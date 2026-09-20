import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyCipher:
    """Versioned authenticated envelopes. The master key never enters the database."""

    _aad = b"short-drama:apikey:v1"

    def __init__(self, key: str | None):
        try:
            decoded = base64.b64decode(key or "", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("ENCRYPTION_KEY must encode exactly 32 bytes") from exc
        if len(decoded) != 32:
            raise ValueError("ENCRYPTION_KEY must encode exactly 32 bytes")
        self._cipher = AESGCM(decoded)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), self._aad)
        return json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "key_version": "v1",
                "nonce": base64.b64encode(nonce).decode(),
                # AESGCM appends its authentication tag to the ciphertext.
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
            separators=(",", ":"),
        )

    def decrypt(self, envelope: str) -> str:
        try:
            value = json.loads(envelope)
            if (value["version"], value["algorithm"], value["key_version"]) != (
                1,
                "AES-256-GCM",
                "v1",
            ):
                raise ValueError("Unsupported key envelope")
            nonce = base64.b64decode(value["nonce"], validate=True)
            ciphertext = base64.b64decode(value["ciphertext"], validate=True)
            return self._cipher.decrypt(nonce, ciphertext, self._aad).decode("utf-8")
        except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError("Invalid encrypted key envelope") from exc

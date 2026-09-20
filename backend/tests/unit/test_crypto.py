import base64
import json
import os

import pytest


def test_encryption_is_randomized_and_authenticated():
    from short_drama.core.crypto import KeyCipher

    cipher = KeyCipher(base64.b64encode(os.urandom(32)).decode())
    first = cipher.encrypt("secret-api-key")
    second = cipher.encrypt("secret-api-key")
    assert first != second
    assert "secret-api-key" not in first
    assert cipher.decrypt(first) == "secret-api-key"
    envelope = json.loads(first)
    envelope["ciphertext"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(ValueError):
        cipher.decrypt(json.dumps(envelope))


def test_missing_or_invalid_encryption_key_fails_closed():
    from short_drama.core.crypto import KeyCipher

    for value in (None, "", "invalid", base64.b64encode(b"short").decode()):
        with pytest.raises(ValueError):
            KeyCipher(value)

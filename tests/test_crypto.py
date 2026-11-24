"""Tests for the Fernet token cipher."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from switchboard.core.crypto import CryptoError, TokenCipher


def _key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip():
    cipher = TokenCipher(_key())
    assert cipher.decrypt(cipher.encrypt("refresh-token-123")) == "refresh-token-123"


def test_ciphertext_hides_plaintext():
    cipher = TokenCipher(_key())
    assert "refresh-token-123" not in cipher.encrypt("refresh-token-123")


def test_encrypt_is_not_deterministic():
    cipher = TokenCipher(_key())
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_wrong_key_fails():
    token = TokenCipher(_key()).encrypt("secret")
    with pytest.raises(CryptoError):
        TokenCipher(_key()).decrypt(token)


def test_corrupt_data_fails():
    with pytest.raises(CryptoError):
        TokenCipher(_key()).decrypt("not-a-fernet-token")


def test_invalid_key_fails():
    with pytest.raises(CryptoError):
        TokenCipher("not-a-valid-key")

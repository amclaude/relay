"""Encryption helpers for secrets stored at rest.

Refresh tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
The key comes from the FERNET_KEY environment variable.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """Raised when a value cannot be encrypted or decrypted."""


class TokenCipher:
    """Encrypt and decrypt short strings with a Fernet key."""

    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise CryptoError(
                "FERNET_KEY is not a valid Fernet key. "
                "Generate one with: python scripts/gen_fernet_key.py"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Return the encrypted value as a URL-safe text string."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Return the original text. Raise CryptoError if the key or data is wrong."""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("Could not decrypt value: wrong key or corrupt data.") from exc

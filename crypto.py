"""
crypto.py — InSync encryption layer
====================================
Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.

WHY NO XOR:
  The original design wrapped Fernet output in a repeating-key XOR layer.
  This is cryptographically harmful, not helpful:

  • Fernet tokens always start with "gAAAAA" (base64 of version byte 0x80).
    XOR-ing a known-prefix ciphertext leaks the first N bytes of the XOR key
    trivially: xor_key[i] = stored[i] ^ "gAAAAA"[i].
    With Fernet's fixed 6-char prefix an attacker recovers 6 key bytes
    with zero brute force; Fernet's timestamp + IV structure give the rest.

  • XOR with a repeating key is a stream cipher with a short, static key.
    Two-time-pad attack: c1 ⊕ c2 = p1 ⊕ p2 (key cancels out), so two
    encrypted messages can be XOR-ed together to cancel the key entirely.

  • XOR is applied to Fernet's *authenticated* output, making the combined
    ciphertext malleable — bit flips survive the XOR layer before Fernet's
    HMAC check, allowing chosen-ciphertext attacks.

  Fernet alone is AES-128-CBC with a random IV + HMAC-SHA256 authentication.
  It is the correct primitive for this use case.
"""

from cryptography.fernet import Fernet, InvalidToken


class InSyncCipher:
    def __init__(self, aes_key: str) -> None:
        key_bytes = aes_key.encode() if isinstance(aes_key, str) else aes_key
        self.fernet = Fernet(key_bytes)

    def encrypt(self, plain_text: str) -> str:
        """Encrypt a UTF-8 string. Returns a URL-safe base64 Fernet token."""
        return self.fernet.encrypt(plain_text.encode()).decode()

    def decrypt(self, token: str) -> str:
        """
        Decrypt a Fernet token. Raises InvalidToken if the token has been
        tampered with or was encrypted with a different key.
        """
        return self.fernet.decrypt(token.encode()).decode()

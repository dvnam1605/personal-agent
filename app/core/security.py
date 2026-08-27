"""Core security and cryptographic helpers."""

import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import PROJECT_ROOT


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure random URL-safe token."""
    return secrets.token_urlsafe(nbytes)


class TokenEncryptionError(ValueError):
    """Raised when an encrypted application token cannot be decrypted."""


def generate_fernet_key() -> str:
    """Generate a URL-safe Fernet key suitable for local secret storage."""
    return Fernet.generate_key().decode("ascii")


class FernetTokenCipher:
    """Encrypt and decrypt OAuth tokens without ever logging their plaintext."""

    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (TypeError, ValueError) as exc:
            raise TokenEncryptionError("Invalid token encryption key.") from exc

    @classmethod
    def from_key_file(cls, path: str | Path) -> "FernetTokenCipher":
        """Load a key from a private local file, creating it atomically on first use."""
        key_path = Path(path).expanduser()
        if not key_path.is_absolute():
            # Relative configured paths anchor to the project root, not the CWD,
            # so the same deployment layout works from any launch directory.
            key_path = PROJECT_ROOT / key_path
        key_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            key = key_path.read_bytes().strip() if key_path.exists() else b""
            if not key:
                generated_key = Fernet.generate_key()
                try:
                    with key_path.open("xb") as handle:
                        handle.write(generated_key)
                    key = generated_key
                except FileExistsError:
                    key = key_path.read_bytes().strip()
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
            if not key:
                raise TokenEncryptionError("Token encryption key file is empty.")
            return cls(key)
        except (OSError, ValueError, TypeError) as exc:
            raise TokenEncryptionError("Unable to load the token encryption key.") from exc

    def encrypt(self, plaintext: str) -> str:
        """Return encrypted token text; plaintext is never included in errors."""
        if not plaintext:
            raise TokenEncryptionError("Cannot encrypt an empty token.")
        try:
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        except (UnicodeError, TypeError) as exc:
            raise TokenEncryptionError("Unable to encrypt token.") from exc

    def decrypt(self, ciphertext: str) -> str:
        """Return plaintext token or a generic error with no ciphertext disclosure."""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, TypeError) as exc:
            raise TokenEncryptionError("Unable to decrypt token.") from exc

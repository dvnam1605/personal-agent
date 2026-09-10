"""Core security and cryptographic helpers."""

import os
import secrets
import subprocess
from pathlib import Path

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import PROJECT_ROOT

logger = structlog.get_logger(__name__)


def _restrict_windows_key_acl(key_path: Path) -> None:
    """Restrict a Fernet key file to the current user on Windows (M6)."""
    user = (os.environ.get("USERNAME") or "").strip()
    if not user:
        logger.warning(
            "token_encryption_key_windows_acl_skipped",
            path=str(key_path),
            hint="USERNAME is unset; restrict the key file with icacls",
        )
        return
    try:
        completed = subprocess.run(
            ["icacls", str(key_path), "/inheritance:r", "/grant:r", f"{user}:R"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "token_encryption_key_icacls_failed",
            path=str(key_path),
            error=str(exc),
        )
        return
    if completed.returncode != 0:
        logger.warning(
            "token_encryption_key_icacls_failed",
            path=str(key_path),
            error=(completed.stderr or completed.stdout or "").strip() or str(completed.returncode),
        )


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure random URL-safe token."""
    return secrets.token_urlsafe(nbytes)


class TokenEncryptionError(ValueError):
    """Raised when an encrypted application token cannot be decrypted."""


def generate_fernet_key() -> str:
    """Generate a URL-safe Fernet key suitable for local secret storage."""
    return Fernet.generate_key().decode("ascii")


class FernetTokenCipher:
    """Symmetric application-layer cipher for OAuth and service credentials."""

    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (TypeError, ValueError) as exc:
            raise TokenEncryptionError("Invalid token encryption key.") from exc

    @classmethod
    def from_settings(cls, settings: object = None) -> "FernetTokenCipher":
        """Load cipher from configured settings or global app settings."""
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings().google
        key = getattr(settings, "token_encryption_key", None)
        if key:
            return cls(key)
        key_path = (
            getattr(settings, "token_encryption_key_file", None) or ".secrets/google_token.key"
        )
        return cls.from_key_file(key_path)

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
                from app.core.config import Environment, get_settings

                app_env = get_settings().environment
                if app_env in (Environment.PRODUCTION, Environment.STAGING):
                    raise TokenEncryptionError(
                        f"Token encryption key file '{key_path}' is missing or empty in {app_env.value}. "
                        "Refusing to auto-generate a new key because existing encrypted tokens would become unrecoverable."
                    )
                generated_key = Fernet.generate_key()
                try:
                    with key_path.open("xb") as handle:
                        handle.write(generated_key)
                    key = generated_key
                    logger.warning(
                        "token_encryption_key_auto_generated",
                        path=str(key_path),
                    )
                except FileExistsError:
                    key = key_path.read_bytes().strip()
            if os.name != "nt":
                try:
                    key_path.chmod(0o600)
                except OSError as chmod_err:
                    logger.warning(
                        "token_encryption_key_chmod_failed",
                        path=str(key_path),
                        error=str(chmod_err),
                    )
            else:
                _restrict_windows_key_acl(key_path)
            if not key:
                raise TokenEncryptionError("Token encryption key file is empty.")
            return cls(key)
        except TokenEncryptionError:
            raise
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

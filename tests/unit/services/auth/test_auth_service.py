"""Unit tests for password hashing parameters and registration races."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.auth.service import (
    DUMMY_PASSWORD_HASH,
    EMAIL_TAKEN_MESSAGE,
    ITERATIONS,
    AuthService,
    hash_password,
    verify_password,
)


def test_new_hashes_use_the_current_iteration_count() -> None:
    hashed = hash_password("mật khẩu đủ dài")
    assert ITERATIONS == 600_000
    assert hashed.startswith("pbkdf2:sha256:600000$")
    assert verify_password("mật khẩu đủ dài", hashed)
    assert not verify_password("sai", hashed)
    assert DUMMY_PASSWORD_HASH.startswith("pbkdf2:sha256:600000$")


def test_hashes_made_with_the_old_iteration_count_still_verify() -> None:
    salt = "0011223344556677"
    key = hashlib.pbkdf2_hmac("sha256", b"old-password", salt.encode(), 100_000)
    legacy = f"pbkdf2:sha256:100000${salt}${key.hex()}"
    assert verify_password("old-password", legacy)
    assert not verify_password("other", legacy)


class _RacingSession:
    """Sees no existing user, then loses the race on commit like a concurrent signup."""

    def __init__(self) -> None:
        self.rolled_back = False

    async def execute(self, _stmt: Any) -> Any:
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        raise IntegrityError("INSERT INTO users", {}, Exception("unique email"))

    async def rollback(self) -> None:
        self.rolled_back = True


async def test_concurrent_duplicate_registration_reports_email_taken() -> None:
    session = _RacingSession()
    with pytest.raises(ValueError, match=EMAIL_TAKEN_MESSAGE):
        await AuthService.register(session, "a@x.vn", "12345678")  # type: ignore[arg-type]
    assert session.rolled_back

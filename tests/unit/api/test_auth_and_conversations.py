"""Unit tests for user authentication and conversations persistence."""

import time
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Conversation, Message, User
from app.services.auth.service import (
    AuthService,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)


def test_password_hashing():
    pw = "SuperSecret123!"
    hashed = hash_password(pw)
    assert hashed.startswith("pbkdf2:sha256:")
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(pw, "invalid$format") is False


def test_session_token_lifecycle():
    user_id = "user-12345"
    email = "test@vov.vn"
    name = "Nguyễn Văn Test"

    token = create_access_token(user_id, email, name)
    assert "." in token

    payload = verify_access_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["email"] == email
    assert payload["name"] == name

    # Tampered token fails
    tampered = token[:-4] + "abcd"
    assert verify_access_token(tampered) is None

    # Invalid format fails
    assert verify_access_token("invalid-token") is None
    assert verify_access_token("") is None


@pytest.mark.asyncio
async def test_auth_service_register_and_login():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # 1. Register
        user = await AuthService.register(
            session=session,
            email="user1@example.com",
            password="password123",
            full_name="User One",
        )
        assert user.id is not None
        assert user.email == "user1@example.com"
        assert user.full_name == "User One"
        assert user.hashed_password is not None

        # 2. Duplicate registration fails
        with pytest.raises(ValueError, match="Email này đã được đăng ký"):
            await AuthService.register(
                session=session,
                email="USER1@example.com",
                password="newpassword",
            )

        # 3. Short password fails
        with pytest.raises(ValueError, match="ít nhất 6 ký tự"):
            await AuthService.register(
                session=session,
                email="user2@example.com",
                password="123",
            )

        # 4. Login success
        logged_in, token = await AuthService.login(
            session=session,
            email="user1@example.com",
            password="password123",
        )
        assert logged_in.id == user.id
        assert token is not None
        verified = verify_access_token(token)
        assert verified["sub"] == user.id

        # 5. Login wrong password
        with pytest.raises(ValueError, match="không chính xác"):
            await AuthService.login(
                session=session,
                email="user1@example.com",
                password="wrong_password",
            )

        # 6. Login non-existent email
        with pytest.raises(ValueError, match="không chính xác"):
            await AuthService.login(
                session=session,
                email="unknown@example.com",
                password="password123",
            )

    await engine.dispose()

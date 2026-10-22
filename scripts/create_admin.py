"""Script to create or reset the administrator account in PostgreSQL."""

import asyncio
import getpass
import os
import sys
import uuid

from sqlalchemy import func, select

# Reconfigure output for Windows UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from app.infrastructure.db.models import User  # noqa: E402
from app.infrastructure.db.session import get_session_factory  # noqa: E402
from app.services.auth.rate_limiter import auth_rate_limiter  # noqa: E402
from app.services.auth.service import hash_password  # noqa: E402

# Credentials come from the environment (or a prompt), never from the source.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@naot.vn")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Quản trị viên Hệ thống")
MIN_PASSWORD_LENGTH = 8


def _admin_password() -> str:
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Mật khẩu admin: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Mật khẩu admin cần ít nhất {MIN_PASSWORD_LENGTH} ký tự.")
    return password


async def main() -> None:
    admin_password = _admin_password()
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = select(User).where(func.lower(User.email) == ADMIN_EMAIL.lower())
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        hashed = hash_password(admin_password)

        if user:
            user.full_name = ADMIN_NAME
            user.hashed_password = hashed
            user.is_active = True
            action = "CẬP NHẬT"
        else:
            user = User(
                id=str(uuid.uuid4()),
                email=ADMIN_EMAIL.lower(),
                full_name=ADMIN_NAME,
                hashed_password=hashed,
                is_active=True,
            )
            session.add(user)
            action = "TẠO MỚI"

        await session.commit()
        await session.refresh(user)

        # Ensure lockout counter in Redis is cleared for admin

        await auth_rate_limiter.clear_login_failures(ADMIN_EMAIL)

        print("=" * 60)
        print(f"THÔNG TIN TÀI KHOẢN ADMIN ({action}):")
        print(f"  - Email       : {user.email}")
        print(f"  - Họ tên      : {user.full_name}")
        print(f"  - User ID     : {user.id}")
        print(f"  - Trạng thái  : {'Kích hoạt' if user.is_active else 'Khóa'}")
        print("=" * 60)
        print("Tài khoản đã sẵn sàng đăng nhập trên hệ thống web!")


if __name__ == "__main__":
    asyncio.run(main())

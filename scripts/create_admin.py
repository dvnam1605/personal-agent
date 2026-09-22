"""Script to create or reset the administrator account in PostgreSQL."""

import asyncio
import sys
import uuid
from sqlalchemy import func, select

# Reconfigure output for Windows UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from app.infrastructure.db.models import User
from app.infrastructure.db.session import get_session_factory
from app.services.auth.rate_limiter import auth_rate_limiter
from app.services.auth.service import hash_password

ADMIN_EMAIL = "admin@naot.vn"
ADMIN_PASSWORD = "Admin@2026!"
ADMIN_NAME = "Quản trị viên Hệ thống"


async def main() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = select(User).where(func.lower(User.email) == ADMIN_EMAIL.lower())
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        hashed = hash_password(ADMIN_PASSWORD)

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
        try:
            await auth_rate_limiter.clear_login_failures(ADMIN_EMAIL)
        except Exception:
            pass

        print("=" * 60)
        print(f"THÔNG TIN TÀI KHOẢN ADMIN ({action}):")
        print(f"  - Email       : {user.email}")
        print(f"  - Mật khẩu    : {ADMIN_PASSWORD}")
        print(f"  - Họ tên      : {user.full_name}")
        print(f"  - User ID     : {user.id}")
        print(f"  - Trạng thái  : {'Kích hoạt' if user.is_active else 'Khóa'}")
        print("=" * 60)
        print("Tài khoản đã sẵn sàng đăng nhập trên hệ thống web!")


if __name__ == "__main__":
    asyncio.run(main())

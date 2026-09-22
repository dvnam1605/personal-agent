"""Authentication routes for user registration, login, and profile lookup."""

import re
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.infrastructure.db.session import get_db_session
from app.services.auth.rate_limiter import MAX_FAILED_LOGIN_ATTEMPTS, auth_rate_limiter
from app.services.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_client_ip(request: Request) -> str:
    """Extract real client IP address considering reverse proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        clean = v.strip().lower()
        if not EMAIL_REGEX.match(clean):
            raise ValueError("Email không đúng định dạng.")
        return clean


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        clean = v.strip().lower()
        if not EMAIL_REGEX.match(clean):
            raise ValueError("Email không đúng định dạng.")
        return clean


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    is_active: bool = True


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AuthResponse:
    client_ip = get_client_ip(request)
    allowed, error_msg, retry_after = await auth_rate_limiter.check_register_allowed(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_msg or "Quá nhiều yêu cầu đăng ký tài khoản.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        user = await AuthService.register(
            session=session,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
        )
        _, token = await AuthService.login(session, body.email, body.password)
        return AuthResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Authenticate and obtain access token",
)
async def login(
    request: Request,
    body: LoginRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AuthResponse:
    client_ip = get_client_ip(request)
    allowed, error_msg, retry_after = await auth_rate_limiter.check_login_allowed(body.email, client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_msg or "Quá nhiều yêu cầu đăng nhập.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        user, token = await AuthService.login(
            session=session,
            email=body.email,
            password=body.password,
        )
        await auth_rate_limiter.clear_login_failures(body.email)
        return AuthResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except ValueError as err:
        failed_count = await auth_rate_limiter.record_login_failure(body.email, client_ip)
        if failed_count >= MAX_FAILED_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Tài khoản đang bị tạm khóa do nhập sai quá {MAX_FAILED_LOGIN_ATTEMPTS} lần liên tiếp. Vui lòng thử lại sau 15 phút.",
                headers={"Retry-After": "900"},
            ) from err

        remaining = MAX_FAILED_LOGIN_ATTEMPTS - failed_count
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Email hoặc mật khẩu không chính xác. Bạn còn {remaining} lần thử trước khi tài khoản bị tạm khóa.",
        ) from err


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> UserResponse:
    user = await AuthService.get_user_by_id(session, user_id)
    if user is None:
        # Fallback for default-user
        return UserResponse(
            id=user_id,
            email="default-user@local.invalid",
            full_name="Default User",
            is_active=True,
        )
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
    )

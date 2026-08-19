"""Canonical application domain errors hierarchy."""

from typing import Any


class AppError(Exception):
    """Base application exception."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class NotFoundError(AppError):
    """Resource not found."""

    def __init__(
        self, message: str = "Resource not found", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="NOT_FOUND", status_code=404, details=details)


class ValidationError(AppError):
    """Input or state validation failed."""

    def __init__(
        self, message: str = "Validation failed", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="VALIDATION_ERROR", status_code=422, details=details)


class AuthenticationError(AppError):
    """Authentication failed or credentials invalid."""

    def __init__(
        self, message: str = "Authentication required", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="UNAUTHENTICATED", status_code=401, details=details)


class PermissionDeniedError(AppError):
    """Access denied to requested resource."""

    def __init__(
        self, message: str = "Permission denied", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            message=message, code="PERMISSION_DENIED", status_code=403, details=details
        )


class PolicyViolationError(AppError):
    """PolicyEngine denied the requested action."""

    def __init__(
        self, message: str = "Policy violation", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="POLICY_VIOLATION", status_code=403, details=details)


class ApprovalRequiredError(AppError):
    """Action requires Human-in-the-Loop approval before execution."""

    def __init__(
        self,
        message: str = "Approval required for sensitive action",
        approval_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        if approval_id:
            d["approval_id"] = approval_id
        super().__init__(message=message, code="APPROVAL_REQUIRED", status_code=202, details=d)


class BudgetExceededError(AppError):
    """Execution exceeded allowed LLM calls, step count, or latency budget."""

    def __init__(
        self, message: str = "Execution budget exceeded", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="BUDGET_EXCEEDED", status_code=429, details=details)


class RateLimitError(AppError):
    """External API or service rate limit reached."""

    def __init__(
        self, message: str = "Rate limit exceeded", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            message=message, code="RATE_LIMIT_EXCEEDED", status_code=429, details=details
        )


class ExternalServiceError(AppError):
    """External provider (Google API, LLM, Web Search) error."""

    def __init__(
        self,
        message: str = "External service failure",
        service_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        if service_name:
            d["service"] = service_name
        super().__init__(message=message, code="EXTERNAL_SERVICE_ERROR", status_code=502, details=d)


class AppTimeoutError(AppError):
    """Operation timed out."""

    def __init__(
        self, message: str = "Operation timed out", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message=message, code="TIMEOUT", status_code=504, details=details)


class ConfigurationError(AppError):
    """Application configuration invalid or missing."""

    def __init__(
        self, message: str = "Invalid configuration", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            message=message, code="CONFIGURATION_ERROR", status_code=500, details=details
        )

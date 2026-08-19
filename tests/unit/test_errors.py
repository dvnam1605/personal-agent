"""Unit tests for domain error hierarchy."""

from app.domain.errors import (
    AppError,
    ApprovalRequiredError,
    AuthenticationError,
    BudgetExceededError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    PolicyViolationError,
    ValidationError,
)


def test_base_app_error():
    err = AppError(
        message="Something broke", code="CUSTOM_CODE", status_code=400, details={"field": "id"}
    )
    assert err.message == "Something broke"
    assert err.code == "CUSTOM_CODE"
    assert err.status_code == 400
    data = err.to_dict()
    assert data["error"]["code"] == "CUSTOM_CODE"
    assert data["error"]["details"]["field"] == "id"


def test_domain_specific_errors():
    nf = NotFoundError("Document not found")
    assert nf.status_code == 404
    assert nf.code == "NOT_FOUND"

    val = ValidationError("Invalid chunk size")
    assert val.status_code == 422
    assert val.code == "VALIDATION_ERROR"

    auth = AuthenticationError()
    assert auth.status_code == 401

    perm = PermissionDeniedError()
    assert perm.status_code == 403

    policy = PolicyViolationError("Sending email denied")
    assert policy.status_code == 403
    assert policy.code == "POLICY_VIOLATION"

    appr = ApprovalRequiredError(approval_id="appr_123")
    assert appr.status_code == 202
    assert appr.details["approval_id"] == "appr_123"

    budget = BudgetExceededError()
    assert budget.status_code == 429

    ext = ExternalServiceError("Google API 503", service_name="gmail")
    assert ext.status_code == 502
    assert ext.details["service"] == "gmail"

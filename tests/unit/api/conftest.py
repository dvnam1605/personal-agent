"""Shared fixtures for the API tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.auth.rate_limiter import AuthRateLimiter, auth_rate_limiter


@pytest.fixture(autouse=True)
def isolated_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh login/register counters per test.

    The limiter uses the developer's live Redis when it is reachable, where
    counters from earlier runs (kept for an hour) would trip the limits.
    """
    prefix = f"assistant:test-{uuid4().hex}:auth_ratelimit"
    monkeypatch.setattr(AuthRateLimiter, "_get_key_prefix", staticmethod(lambda: prefix))
    auth_rate_limiter._memory_store.clear()

"""Timezone normalization shared by services that read timestamps back from the database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import overload


@overload
def as_utc(value: datetime) -> datetime: ...


@overload
def as_utc(value: None) -> None: ...


def as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as an aware UTC datetime.

    SQLite (used in tests and CI) drops timezone metadata, so a naive value is taken
    to be UTC, which is how every timestamp is stored. Aware values are converted.
    """
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

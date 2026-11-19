"""Routines: requests the user saved to run on a schedule."""

import uuid
from datetime import date, time

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base, TimestampMixin


class Routine(Base, TimestampMixin):
    """A user-defined skill: a request run at ``run_time`` on ``weekdays``.

    ``weekdays`` holds Python weekday numbers ("0,1,2,3,4" is Monday–Friday).
    ``last_run_on`` makes each scheduled day run once.
    """

    __tablename__ = "routines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    weekdays: Mapped[str] = mapped_column(String(20), nullable=False)
    run_time: Mapped[time] = mapped_column(Time, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (Index("ix_routines_user_active", "user_id", "active"),)

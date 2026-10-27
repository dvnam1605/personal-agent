"""Proactive worker settings: when the morning briefing and meeting preparation run."""

from datetime import time

from pydantic import BaseModel, Field


class ProactiveSettings(BaseModel):
    """Schedule of the background jobs that act without being asked.

    Times are wall-clock times in ``timezone``. The worker wakes every
    ``tick_seconds`` and each job decides for itself whether it is due, so a
    restart never loses a run; a briefing missed for longer than
    ``briefing_catch_up_hours`` is skipped instead of arriving in the afternoon.
    """

    enabled: bool = Field(default=True, description="Run the proactive worker in the backend")
    timezone: str = Field(default="Asia/Ho_Chi_Minh", description="Local time zone of the jobs")
    tick_seconds: float = Field(default=60.0, gt=0.0, description="Worker wake-up interval")
    briefing_enabled: bool = Field(default=True, description="Send the morning briefing")
    briefing_time: time = Field(default=time(7, 30), description="Local time of the briefing")
    briefing_catch_up_hours: float = Field(
        default=4.0, gt=0.0, description="How late a missed briefing may still be sent"
    )
    meeting_prep_enabled: bool = Field(default=True, description="Prepare upcoming meetings")
    meeting_prep_lead_minutes: int = Field(
        default=30, ge=5, le=240, description="How long before a meeting its dossier is built"
    )
    failure_backoff_seconds: float = Field(
        default=900.0, gt=0.0, description="Pause for a user's job after it fails"
    )
    notification_retention_days: int = Field(
        default=30, gt=0, description="Age after which notifications are deleted"
    )

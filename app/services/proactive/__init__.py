"""Proactive assistant: jobs that run without a request and leave notifications.

The worker wakes on a fixed tick, runs the morning briefing, meeting preparation,
meeting follow-up suggestions and the mail watch for every user with Google
connected, stores the results as notifications and announces them on a pub/sub
bus for open tabs. Jobs only read; anything that would write to Google still has to
become an approval request.
"""

from app.services.proactive.briefing import MorningBriefingJob
from app.services.proactive.events import (
    InMemoryNotificationBus,
    NotificationBus,
    RedisNotificationBus,
    get_notification_bus,
    set_notification_bus,
)
from app.services.proactive.followup import MeetingFollowupJob
from app.services.proactive.mail_watch import MailWatchJob
from app.services.proactive.meeting_prep import MeetingPrepJob
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import OrchestratorSources, ProactiveSources
from app.services.proactive.worker import ProactiveJob, ProactiveWorker

__all__ = [
    "InMemoryNotificationBus",
    "MailWatchJob",
    "MeetingFollowupJob",
    "MeetingPrepJob",
    "MorningBriefingJob",
    "NotificationBus",
    "NotificationService",
    "OrchestratorSources",
    "ProactiveJob",
    "ProactiveSources",
    "ProactiveWorker",
    "RedisNotificationBus",
    "get_notification_bus",
    "set_notification_bus",
]

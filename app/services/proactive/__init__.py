"""Proactive assistant: jobs that run without a request and leave notifications.

The worker wakes on a fixed tick, runs the morning briefing, meeting preparation,
meeting follow-up suggestions and the mail watch for every user with Google
connected, stores the results as notifications and announces them on a pub/sub
bus for open tabs. The jobs themselves live in ``proactive.jobs``. Jobs only read;
anything that would write to Google still has to become an approval request.
"""

from app.services.proactive.events import (
    InMemoryNotificationBus,
    NotificationBus,
    RedisNotificationBus,
    get_notification_bus,
    set_notification_bus,
)
from app.services.proactive.jobs import (
    GoalReminderJob,
    MailWatchJob,
    MeetingFollowupJob,
    MeetingMinutesJob,
    MeetingPrepJob,
    MorningBriefingJob,
    RoutineJob,
    WatcherJob,
)
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import OrchestratorSources, ProactiveSources
from app.services.proactive.worker import ProactiveJob, ProactiveWorker

__all__ = [
    "GoalReminderJob",
    "InMemoryNotificationBus",
    "MailWatchJob",
    "MeetingFollowupJob",
    "MeetingMinutesJob",
    "MeetingPrepJob",
    "MorningBriefingJob",
    "NotificationBus",
    "NotificationService",
    "OrchestratorSources",
    "ProactiveJob",
    "ProactiveSources",
    "ProactiveWorker",
    "RedisNotificationBus",
    "RoutineJob",
    "WatcherJob",
    "get_notification_bus",
    "set_notification_bus",
]

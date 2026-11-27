"""Proactive jobs: each one reads the user's sources and leaves notifications.

Shared plumbing (worker, notification storage, pub/sub bus, sources, formatting)
lives one level up in ``app.services.proactive``; this package holds only the jobs.
"""

from app.services.proactive.jobs.briefing import MorningBriefingJob
from app.services.proactive.jobs.followup import MeetingFollowupJob
from app.services.proactive.jobs.goals import GoalReminderJob
from app.services.proactive.jobs.mail_watch import MailWatchJob
from app.services.proactive.jobs.meeting_prep import MeetingPrepJob
from app.services.proactive.jobs.minutes import MeetingMinutesJob
from app.services.proactive.jobs.routines import RoutineJob
from app.services.proactive.jobs.watcher import WatcherJob

__all__ = [
    "GoalReminderJob",
    "MailWatchJob",
    "MeetingFollowupJob",
    "MeetingMinutesJob",
    "MeetingPrepJob",
    "MorningBriefingJob",
    "RoutineJob",
    "WatcherJob",
]

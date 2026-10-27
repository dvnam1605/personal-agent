"""Proactive assistant: jobs that run without a request and leave notifications.

The worker wakes on a fixed tick, runs the morning briefing and the meeting
preparation for every user with Google connected, and stores the results as
notifications. Jobs only read; anything that would write to Google still has to
become an approval request.
"""

from app.services.proactive.briefing import MorningBriefingJob
from app.services.proactive.meeting_prep import MeetingPrepJob
from app.services.proactive.notifications import NotificationService
from app.services.proactive.sources import OrchestratorSources, ProactiveSources
from app.services.proactive.worker import ProactiveJob, ProactiveWorker

__all__ = [
    "MeetingPrepJob",
    "MorningBriefingJob",
    "NotificationService",
    "OrchestratorSources",
    "ProactiveJob",
    "ProactiveSources",
    "ProactiveWorker",
]

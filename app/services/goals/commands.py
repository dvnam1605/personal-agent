"""Recognise goal and watch requests in chat."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.services.skills.matching import unaccent_vietnamese
from app.services.tasks.commands import split_due

GoalCommandKind = Literal[
    "goal_create",
    "goal_progress",
    "goal_step_done",
    "watch_create",
    "watch_list",
    "watch_stop",
]
WatchKind = Literal["mail_from", "mail_keyword", "document_topic"]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Matched on accent-folded text; folding keeps offsets, so captured text keeps diacritics.
_GOAL_CREATE = re.compile(r"^\s*(?:dat|tao|them|lap) muc tieu\s*[:,]?\s*(?P<rest>\S.*)$")
_GOAL_PROGRESS = re.compile(
    r"\b(?:tien do|tinh hinh)(?: cac| cua)? muc tieu\b|^\s*(?:cac |nhung )?muc tieu (?:cua (?:toi|minh)|dang co)\b"
)
_STEP_DONE = re.compile(
    r"^\s*(?:da )?(?:xong|hoan thanh) buoc (?P<n>\d+)(?: (?:cua|trong) muc tieu (?P<goal>\S.*))?\s*$"
)
_WATCH_LIST = re.compile(
    r"\b(?:dang|nhung gi|nhung ai) (?:duoc )?theo doi\b|^\s*danh sach theo doi\b"
)
_WATCH_STOP = re.compile(r"^\s*(?:dung|bo|huy|ngung) theo doi\s*[:,]?\s*(?P<target>.*)$")
_WATCH_CREATE = re.compile(
    r"^\s*(?:hay )?(?:theo doi|bao (?:cho )?(?:toi|minh) (?:khi|neu)|canh bao (?:toi |minh )?khi)"
    r"\s*[:,]?\s*(?P<rest>\S.*)$"
)
_DOCUMENT = re.compile(r"\b(?:tai lieu|van ban|quyet dinh|cong van)\b.*?\bve\s+(?P<topic>\S.*)$")
_QUOTED = re.compile(r"[«\"“'](?P<phrase>[^»\"”']{2,80})[»\"”']")
_KEYWORD = re.compile(
    r"\b(?:email|mail|thu)\b.*?\b(?:co|nhac den|chua|noi ve|ve)\s+(?P<phrase>\S.*)$"
)


@dataclass(frozen=True)
class GoalCommand:
    """A chat turn about goals or watch rules."""

    kind: GoalCommandKind
    title: str = ""
    due: date | None = None
    step: int | None = None
    watch_kind: WatchKind | None = None
    target: str = ""


def parse_goal_command(query: str, today: date) -> GoalCommand | None:
    """Recognise goal and watch requests; ``None`` for anything else."""
    folded = unaccent_vietnamese(query)
    if match := _GOAL_CREATE.match(folded):
        title, due = split_due(query[match.start("rest") :], today)
        return GoalCommand("goal_create", title=title, due=due) if title else None
    if match := _STEP_DONE.match(folded):
        goal = query[match.start("goal") :].strip() if match.group("goal") else ""
        return GoalCommand("goal_step_done", step=int(match.group("n")), title=goal)
    if _GOAL_PROGRESS.search(folded):
        return GoalCommand("goal_progress")
    if _WATCH_LIST.search(folded):
        return GoalCommand("watch_list")
    if match := _WATCH_STOP.match(folded):
        return GoalCommand("watch_stop", target=query[match.start("target") :].strip())
    if match := _WATCH_CREATE.match(folded):
        return _watch(query, match.start("rest"))
    return None


def _watch(query: str, start: int) -> GoalCommand | None:
    """Classify what to watch: a sender's mail, a phrase in mail, or new documents."""
    rest = query[start:].strip().rstrip(".!?")
    folded = unaccent_vietnamese(rest)
    if match := _DOCUMENT.search(folded):
        topic = rest[match.start("topic") :].strip()
        return GoalCommand("watch_create", watch_kind="document_topic", target=topic[:255])
    quoted = _QUOTED.search(rest)
    if quoted and re.search(r"\b(?:email|mail|thu)\b", folded):
        return GoalCommand("watch_create", watch_kind="mail_keyword", target=quoted.group("phrase"))
    if addresses := _EMAIL.findall(rest):
        return GoalCommand("watch_create", watch_kind="mail_from", target=addresses[0].lower())
    if match := _KEYWORD.search(folded):
        phrase = rest[match.start("phrase") :].strip()
        return GoalCommand("watch_create", watch_kind="mail_keyword", target=phrase[:255])
    return None

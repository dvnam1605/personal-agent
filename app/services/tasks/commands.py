"""Recognise to-do requests in chat and read a due date out of Vietnamese text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.services.skills.matching import unaccent_vietnamese

TaskCommandKind = Literal["list", "create", "complete"]

# Matched on accent-folded text; folding keeps offsets, so titles keep their diacritics.
_LIST = re.compile(
    r"^\s*(?:xem |liet ke |cho (?:toi|minh) xem )?(?:cac |danh sach |nhung )?"
    r"(?:viec can lam|cong viec can lam|viec phai lam|todo|to-do|tasks?)"
    r"(?: cua (?:toi|minh))?(?: (?:hom nay|tuan nay|con lai))?\s*\??\s*$"
    r"|\b(?:toi|minh) (?:con|co) (?:viec|task) (?:gi|nao)\b"
    # "check task xem tuần này có task gì", "có bao nhiêu task", "xem danh sách task"
    r"|\b(?:check|kiem tra|xem|liet ke|danh sach)\b.*\b(?:tasks?|todo|viec can lam)\b"
    r"|\b(?:co|con) (?:bao nhieu )?(?:tasks?|todo|viec can lam)(?: (?:gi|nao))?\b"
)
_CREATE = re.compile(
    r"^\s*(?:(?:them|tao|ghi)(?: (?:mot|1))? (?:viec|task|cong viec|todo|viec can lam)"
    r"|nhac (?:toi|minh)(?: viec)?)\s*[:,]?\s*(?P<title>\S.*)$"
)
_COMPLETE = re.compile(
    r"^\s*(?:da xong|xong|hoan thanh|danh dau xong|danh dau hoan thanh)"
    r"(?: (?:viec|task|cong viec))?\s*[:,]?\s*(?P<title>\S.*)$"
)
_COMMANDS: tuple[tuple[TaskCommandKind, re.Pattern[str]], ...] = (
    ("create", _CREATE),
    ("complete", _COMPLETE),
)
_WEEKDAYS = {
    "2": 0, "hai": 0, "3": 1, "ba": 1, "4": 2, "tu": 2,
    "5": 3, "nam": 3, "6": 4, "sau": 4, "7": 5, "bay": 5,
}  # fmt: skip
_DUE = re.compile(
    r"\s*[,;-]?\s*(?:(?:han|truoc|vao|deadline|trong)\s+)?(?:ngay\s+)?"
    r"(?P<when>hom nay|ngay mai|mai|ngay kia|chu nhat|thu (?:2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)"
    r"|(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{4}))?)\b"
)


_FILLER = frozenset({"roi", "nhe", "nha", "a", "ok", "xong"})
# Words after which "mai" names a person: prepositions, verbs taking a person, kinship titles.
_PERSON_CUES = frozenset(
    {
        "voi", "gap", "cho", "gui", "goi", "nhan", "hoi", "don", "moi", "cung", "giup",
        "anh", "chi", "em", "ban", "co", "chu", "bac", "ong", "ba", "di", "thay", "sep",
    }
)  # fmt: skip


@dataclass(frozen=True)
class TaskCommand:
    """A chat turn about the user's to-do list."""

    kind: TaskCommandKind
    title: str = ""
    due: date | None = None


def parse_task_command(query: str, today: date) -> TaskCommand | None:
    """Recognise "việc cần làm", "thêm việc: … hạn thứ 6", "xong việc …"; ``None`` otherwise."""
    folded = unaccent_vietnamese(query)
    # Add/complete first: their titles may contain list words ("thêm việc: check task").
    for kind, pattern in _COMMANDS:
        match = pattern.match(folded)
        if match is None:
            continue
        start = match.start("title")
        title, due = split_due(query[start:], today)
        if not title or unaccent_vietnamese(title) in _FILLER:
            return None
        return TaskCommand(kind, title=title, due=due if kind == "create" else None)
    if _LIST.search(folded):
        return TaskCommand("list")
    return None


def split_due(text: str, today: date) -> tuple[str, date | None]:
    """Remove the last due-date phrase from ``text`` and return (title, due date)."""
    folded = unaccent_vietnamese(text)
    matches = [match for match in _DUE.finditer(folded) if not _is_name(folded, match)]
    if not matches:
        return _clean(text), None
    match = matches[-1]
    due = _resolve(match, today)
    title = text[: match.start()] + text[match.end() :]
    return _clean(title), due


def _is_name(folded: str, match: re.Match[str]) -> bool:
    """Whether a bare "mai" is the name Mai rather than "tomorrow".

    Case says nothing (people type "họp với mai"), so the word before decides: after a
    word that introduces a person ("với", "gặp", "chị"…) it is a name; with an explicit
    time word ("ngày mai", "hạn mai") or anywhere else it is tomorrow.
    """
    if match.group("when") != "mai" or match.group(0).strip(" ,;-") != "mai":
        return False
    before = folded[: match.start("when")].split()
    return bool(before) and before[-1] in _PERSON_CUES


def _resolve(match: re.Match[str], today: date) -> date | None:
    when = match.group("when")
    if match.group("d"):
        day, month = int(match.group("d")), int(match.group("m"))
        year = int(match.group("y")) if match.group("y") else today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if not match.group("y") and candidate < today:
            candidate = date(year + 1, month, day)
        return candidate
    if when == "hom nay":
        return today
    if when in ("ngay mai", "mai"):
        return today + timedelta(days=1)
    if when == "ngay kia":
        return today + timedelta(days=2)
    target = 6 if when == "chu nhat" else _WEEKDAYS[when.split()[-1]]
    days = (target - today.weekday()) % 7 or 7
    return today + timedelta(days=days)


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(" ,;:-.")

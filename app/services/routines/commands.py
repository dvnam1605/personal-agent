"""Recognise routine requests and read a weekly schedule out of Vietnamese text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from typing import Literal

from app.services.skills.matching import unaccent_vietnamese

RoutineCommandKind = Literal["create", "list", "delete"]
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)
WORKDAYS = (0, 1, 2, 3, 4)

# Matched on accent-folded text; folding keeps offsets, so the saved request keeps its
# diacritics. The schedule runs up to the first ":" that is not part of a time ("8:30").
_CREATE = re.compile(
    r"^\s*(?:tao|them|dat|lap) (?:quy trinh|routine|thoi quen|lich tu dong)\s*[:,]?\s*"
    r"(?P<schedule>(?:\d{1,2}:\d{2}|[^:])+?)\s*:\s*(?P<query>\S.*)$"
)
_LIST = re.compile(
    r"\b(?:cac|danh sach|nhung) (?:quy trinh|routine)\b|^\s*quy trinh cua (?:toi|minh)\b"
)
_DELETE = re.compile(r"^\s*(?:xoa|huy|tat|dung) (?:quy trinh|routine)\s*[:,]?\s*(?P<target>\S.*)$")
_TIME = re.compile(r"\b(?P<h>\d{1,2})\s*(?:h|gio|:)\s*(?P<m>\d{2})?\b")
_DAY_WORDS = {"hai": 0, "2": 0, "ba": 1, "3": 1, "tu": 2, "4": 2, "nam": 3, "5": 3, "sau": 4, "6": 4, "bay": 5, "7": 5}  # fmt: skip
_WEEKDAY = re.compile(r"\bthu (hai|ba|tu|nam|sau|bay|[2-7])\b")


@dataclass(frozen=True)
class RoutineCommand:
    """A chat turn about routines."""

    kind: RoutineCommandKind
    query: str = ""
    weekdays: tuple[int, ...] = ()
    run_time: time | None = None
    target: str = ""


def parse_schedule(text: str) -> tuple[tuple[int, ...], time] | None:
    """Weekdays and time from "mỗi thứ 6 lúc 17h", "hằng ngày 8:30", "ngày làm việc 7h"."""
    folded = unaccent_vietnamese(text)
    clock = _TIME.search(folded)
    if clock is None:
        return None
    hour, minute = int(clock.group("h")), int(clock.group("m") or 0)
    if re.search(r"\b(?:chieu|toi)\b", folded) and hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    if re.search(r"\b(?:ngay lam viec|thu 2 den thu 6|thu hai den thu sau)\b", folded):
        days: tuple[int, ...] = WORKDAYS
    else:
        found = {_DAY_WORDS[word] for word in _WEEKDAY.findall(folded)}
        if re.search(r"\bchu nhat\b", folded):
            found.add(6)
        days = tuple(sorted(found)) if found else ALL_DAYS
    return days, time(hour, minute)


def parse_routine_command(query: str) -> RoutineCommand | None:
    """Recognise create / list / delete routine requests; ``None`` otherwise."""
    folded = unaccent_vietnamese(query)
    if match := _CREATE.match(folded):
        schedule = parse_schedule(query[match.start("schedule") : match.end("schedule")])
        request = query[match.start("query") :].strip()
        if schedule is None or not request:
            return None
        return RoutineCommand("create", query=request, weekdays=schedule[0], run_time=schedule[1])
    if match := _DELETE.match(folded):
        return RoutineCommand("delete", target=query[match.start("target") :].strip())
    if _LIST.search(folded):
        return RoutineCommand("list")
    return None


def describe(weekdays: tuple[int, ...] | list[int], run_time: time) -> str:
    """Human schedule: "hằng ngày lúc 08:00", "thứ 2–6 lúc 07:30", "thứ 6 lúc 17:00"."""
    days = tuple(weekdays)
    if days == ALL_DAYS:
        when = "hằng ngày"
    elif days == WORKDAYS:
        when = "thứ 2–6"
    else:
        when = ", ".join("chủ nhật" if day == 6 else f"thứ {day + 2}" for day in days)
    return f"{when} lúc {run_time:%H:%M}"

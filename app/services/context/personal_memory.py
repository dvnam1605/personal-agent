"""What the assistant remembers about its user: chat commands, storage policy and context.

The user stays in control of long-term memory. Nothing is remembered silently from
ordinary questions: an item is stored when the user says so in chat ("nhớ rằng …"),
adds it on the memory page, or confirms a preference. Active items are given to the
model as a short, clearly labelled block so a draft can use the user's name, signature
or tone, and can be listed, edited and deleted at any time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.domain.enums import MemoryType
from app.domain.models.context.memory import MemoryItem
from app.services.context.memory_store import MemoryStore
from app.services.context.preference_service import PreferenceService
from app.services.skills.matching import unaccent_vietnamese

MAX_CONTENT_LEN = 500
MAX_PREFERENCES_IN_CONTEXT = 10
MAX_FACTS_IN_CONTEXT = 5
MAX_FORGET = 3
_SIGNATURE_VALUE = re.compile(r"(?:chữ ký|chu ky)[^:]*?(?::|\blà\b|\bla\b)\s*(?P<value>.+)$", re.I)
_DEICTIC = frozenset({"do", "nay", "kia", "cai", "chuyen", "dieu", "viec", "ay", "nhe"})

# Matched on accent-folded, lower-cased text; folding keeps offsets, so the content is
# cut from the original string and keeps its diacritics.
_REMEMBER = re.compile(
    r"^\s*(?:hay |ban |lam on |nho ban )?(?:ghi nho|nho giup(?: toi| minh)?|nho (?:rang|la))\b"
    r"\s*(?:(?:rang|la)\b)?\s*[:,]?\s*(?P<content>\S.*)$"
)
_FORGET = re.compile(
    r"^\s*(?:(?:hay |ban )quen(?: di)?|quen di|quen(?=\s*(?::|rang\b|la\b|ve\b|dieu\b|chuyen\b))"
    r"|xoa (?:ghi nho|tri nho)(?: ve)?|dung nho)\b"
    r"\s*(?:(?:rang|la|ve|dieu|chuyen)\b)?\s*[:,]?\s*(?P<content>\S.*)$"
)
_RECALL = re.compile(
    r"\b(?:ban|tro ly|noat) (?:dang )?(?:nho|biet) (?:nhung )?(?:gi|dieu gi) ve (?:toi|minh)\b"
)
_PREFERENCE = re.compile(
    r"^(?:toi |minh |em )?(?:thich|khong thich|muon|uu tien|ghet|hay|luon|dung|khong muon)\b"
    r"|\b(?:giong van|van phong|chu ky|xung ho|ngon ngu|gio lam viec)\b"
)
# Preferences in the same category replace each other ("chữ ký" today supersedes yesterday's).
_CATEGORIES = {
    "chu ky": "signature",
    "giong van": "tone",
    "van phong": "tone",
    "xung ho": "addressing",
    "ngon ngu": "language",
    "gio lam viec": "working_hours",
}


MemoryCommandKind = Literal["remember", "forget", "recall"]


@dataclass(frozen=True)
class MemoryCommand:
    """A chat turn that manages memory instead of asking something."""

    kind: MemoryCommandKind
    content: str = ""


_COMMAND_PATTERNS: tuple[tuple[MemoryCommandKind, re.Pattern[str]], ...] = (
    ("remember", _REMEMBER),
    ("forget", _FORGET),
)


@dataclass(frozen=True)
class ForgetResult:
    """Items forgotten, or the candidates when the request matched too many to be sure."""

    forgotten: list[MemoryItem]
    ambiguous: list[MemoryItem]


def parse_memory_command(query: str) -> MemoryCommand | None:
    """Recognise "nhớ rằng …", "quên …" and "bạn nhớ gì về tôi"; ``None`` otherwise."""
    folded = unaccent_vietnamese(query)
    if _RECALL.search(folded):
        return MemoryCommand("recall")
    for kind, pattern in _COMMAND_PATTERNS:
        match = pattern.match(folded)
        if match:
            content = query[match.start("content") :].strip().rstrip(".!")
            if content:
                return MemoryCommand(kind, content[:MAX_CONTENT_LEN])
    return None


class PersonalMemoryService:
    """Remember, forget and recall a user's facts and preferences; build the model context."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._preferences = PreferenceService(store)

    async def remember(self, user_id: str, content: str) -> MemoryItem:
        """Store one fact or preference; a preference replaces the previous one of its kind."""
        text = content.strip()[:MAX_CONTENT_LEN]
        folded = unaccent_vietnamese(text)
        if _PREFERENCE.search(folded):
            category = next(
                (name for key, name in _CATEGORIES.items() if key in folded), f"note:{folded}"
            )
            return await self._preferences.record_preference(user_id, text, category=category)
        item = MemoryItem(
            user_id=user_id,
            memory_type=MemoryType.FACT,
            content=text,
            importance=0.7,
            metadata={"source": "chat"},
        )
        return await self._store.save_memory(item)

    async def forget(self, user_id: str, content: str) -> ForgetResult:
        """Mark stale the active items that contain every meaningful word of ``content``.

        Deictic words ("đó", "này") carry no meaning here, and a description matching more
        than ``MAX_FORGET`` items forgets nothing: the user is asked to be more specific
        rather than losing several memories to a vague request.
        """
        words = [
            word
            for word in unaccent_vietnamese(content).split()
            if len(word) > 1 and word not in _DEICTIC
        ]
        if not words:
            return ForgetResult(forgotten=[], ambiguous=[])
        matches = [
            item
            for item in await self._store.list_memories(user_id)
            if all(word in unaccent_vietnamese(item.content) for word in words)
        ]
        if len(matches) > MAX_FORGET:
            return ForgetResult(forgotten=[], ambiguous=matches)
        forgotten = [item for item in matches if await self._store.mark_stale(item.id)]
        return ForgetResult(forgotten=forgotten, ambiguous=[])

    async def active(self, user_id: str) -> list[MemoryItem]:
        """Active preferences and facts, newest first."""
        items = await self._store.list_memories(user_id)
        return [
            item for item in items if item.memory_type in (MemoryType.PREFERENCE, MemoryType.FACT)
        ]

    async def signature(self, user_id: str) -> str | None:
        """The remembered signature text ("chữ ký của tôi là X" → "X"), if any."""
        for item in await self._preferences.get_active_preferences(user_id, category="signature"):
            match = _SIGNATURE_VALUE.search(item.content)
            value = (match.group("value") if match else "").strip().strip("«»\"'")
            if value:
                return value
        return None

    async def context_for(self, user_id: str, query: str) -> str:
        """Labelled block of the user's preferences and the facts relevant to ``query``."""
        preferences = (await self._preferences.get_active_preferences(user_id))[
            :MAX_PREFERENCES_IN_CONTEXT
        ]
        facts = await self._store.search_memories(
            user_id, query, memory_type=MemoryType.FACT, limit=MAX_FACTS_IN_CONTEXT
        )
        if not preferences and not facts:
            return ""
        lines = ["Thông tin người dùng đã nhờ trợ lý ghi nhớ (dùng khi phù hợp):"]
        lines.extend(f"- {item.content}" for item in [*preferences, *facts])
        return "\n".join(lines)


def recall_message(items: list[MemoryItem]) -> str:
    """Chat answer listing what is remembered."""
    if not items:
        return (
            "Tôi chưa ghi nhớ điều gì về bạn. Bạn có thể nói «nhớ rằng …» "
            "hoặc thêm trong trang Bộ nhớ."
        )
    lines = ["Những điều tôi đang ghi nhớ về bạn:"]
    lines.extend(f"- {item.content}" for item in items)
    lines.append("")
    lines.append("Nói «quên …» hoặc vào trang Bộ nhớ để sửa, xóa.")
    return "\n".join(lines)

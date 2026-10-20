"""Citation labels and numbering shared by streaming and non-streaming synthesis.

The model sees short labels (``E1`` … ``En``) instead of 32-hex evidence
ids: they are cheaper, harder to garble, and impossible to confuse with
content. A :class:`CitationNumberer` maps every label the model writes to a
footnote number in order of first use; labels that are not in the bundle
(hallucinated or mistyped) are dropped and never get a number, so the
inline ``[n]`` badges always match the returned citation list. Legacy 32-hex
ids are still accepted for older prompts and tools.
"""

from __future__ import annotations

import re

from app.domain.models.retrieval import EvidenceBundle

_TOKEN = r"(?:E\d{1,3}|[0-9a-fA-F]{32})"
CITATION_BRACKET_RE = re.compile(
    rf"\[\s*(?:evidence_id=)?[\"']?{_TOKEN}[\"']?(?:\s*[,;]?\s*(?:evidence_id=)?[\"']?{_TOKEN}[\"']?)*\s*\]"
)
_TOKEN_RE = re.compile(_TOKEN)
_ORPHAN_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")

REFUSAL_RE = re.compile(
    r"(?i)\b(?:không tìm thấy|không có thông tin|chưa có thông tin|không đủ (?:thông tin|bằng chứng)|"
    r"tài liệu không đề cập|không được đề cập)\b"
)


def evidence_label(index: int) -> str:
    """Label shown to the model for the ``index``-th bundle item (0-based)."""
    return f"E{index + 1}"


class CitationNumberer:
    """Stateful label → footnote-number mapping for one answer."""

    def __init__(self, bundle: EvidenceBundle) -> None:
        self._eid_by_token: dict[str, str] = {}
        for idx, item in enumerate(bundle.items):
            self._eid_by_token[evidence_label(idx)] = item.evidence_id
            self._eid_by_token[item.evidence_id.lower()] = item.evidence_id
        self._number_by_eid: dict[str, int] = {}
        self.cited_ids: list[str] = []
        self.dropped: list[str] = []

    def _resolve(self, token: str) -> str | None:
        key = token.upper() if token[:1] in "eE" and len(token) <= 4 else token.lower()
        return self._eid_by_token.get(key)

    def render_bracket(self, bracket: str) -> str:
        """``[E2, E5]`` → ``[1][2]``; unknown labels vanish."""
        badges: list[str] = []
        for token in _TOKEN_RE.findall(bracket):
            eid = self._resolve(token)
            if eid is None:
                self.dropped.append(token)
                continue
            if eid not in self._number_by_eid:
                self._number_by_eid[eid] = len(self._number_by_eid) + 1
                self.cited_ids.append(eid)
            badge = f"[{self._number_by_eid[eid]}]"
            if badge not in badges:
                badges.append(badge)
        return "".join(badges)

    def rewrite(self, text: str) -> str:
        """Rewrite every citation bracket in a complete answer."""
        out = CITATION_BRACKET_RE.sub(lambda m: self.render_bracket(m.group(0)), text)
        out = _ORPHAN_HEX_RE.sub("", out)
        out = re.sub(r"\[\s*\]", "", out)
        return re.sub(r"[ \t]+([.,;:])", r"\1", out)

    @staticmethod
    def looks_like_citation(fragment: str) -> bool:
        return bool(CITATION_BRACKET_RE.fullmatch(fragment))

    @staticmethod
    def mentions_evidence(fragment: str) -> bool:
        return bool(_TOKEN_RE.search(fragment))


def strip_orphan_ids(text: str) -> str:
    return _ORPHAN_HEX_RE.sub("", text)

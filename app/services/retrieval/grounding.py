"""Deterministic grounding checks around answer synthesis (no extra LLM call).

* **Before** synthesis: if the question names a precise identifier (a
  document number like ``80/QĐ-…``, a code, an amount) that occurs in none
  of the retrieved evidence, the evidence is about something else. Abstain
  with a structured "found / missing" reply instead of letting the model
  answer from look-alike documents — this also saves a gateway call.
* **After** synthesis: sentences stating data (amounts, percentages, dates,
  multi-digit figures) without any citation badge are "unsupported"; their
  presence caps the answer status at PARTIAL and is reported for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval import EvidenceBundle

# Identifiers precise enough that their absence from all evidence is decisive.
_IDENTIFIER_RE = re.compile(
    r"\b\d{1,5}\s*/\s*(?:\d{4}\s*/\s*)?[A-Za-zĐđ][\wĐđ\-/]*"  # 80/QĐ-TNVN, 45/2026/HĐDV
    r"|\b[A-Za-zĐđ]{2,}-\d{2,}[\w\-]*"  # HĐ-2026-15, REQ-104
)
_DATA_RE = re.compile(
    r"\d+[.,]\d{3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?\s*%|\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{3,}\b"
)
_BADGE_RE = re.compile(r"\[\d+\]")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unaccent_vietnamese(text))


@dataclass
class PreSynthesisVerdict:
    abstain: bool
    missing_identifiers: list[str] = field(default_factory=list)


def check_identifiers(question: str, bundle: EvidenceBundle) -> PreSynthesisVerdict:
    identifiers = [m.group(0).strip() for m in _IDENTIFIER_RE.finditer(question)]
    if not identifiers or not bundle.items:
        return PreSynthesisVerdict(abstain=False)
    haystack = _norm(
        " ".join(
            f"{item.content_raw} {item.anchors.get('doc_number') or ''} {item.title or ''}"
            for item in bundle.items
        )
    )
    missing = [ident for ident in identifiers if _norm(ident) not in haystack]
    # Abstain only when *every* named identifier is absent: one match means the
    # evidence is at least about the right document.
    return PreSynthesisVerdict(
        abstain=len(missing) == len(identifiers), missing_identifiers=missing
    )


def abstain_message(verdict: PreSynthesisVerdict, bundle: EvidenceBundle) -> str:
    titles: list[str] = []
    for item in bundle.items:
        title = item.anchors.get("profile_title") or item.title or item.filename
        if title and title not in titles:
            titles.append(str(title))
    lines = [
        "**Chưa có trong tài liệu:** không tìm thấy "
        + ", ".join(f"**{ident}**" for ident in verdict.missing_identifiers)
        + " trong các tài liệu được truy xuất.",
    ]
    if titles:
        lines.append("\n**Đã tìm thấy (có thể liên quan):**")
        lines.extend(f"- {t}" for t in titles[:5])
    lines.append(
        "\nHãy kiểm tra lại số hiệu/mã, hoặc hỏi theo nội dung để tôi tìm trên toàn bộ kho tài liệu."
    )
    return "\n".join(lines)


@dataclass
class GroundingReport:
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported


def check_answer(answer: str) -> GroundingReport:
    """Sentences that state data but carry no citation badge."""
    unsupported: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(answer):
        sentence = raw.strip()
        if not sentence or sentence.startswith(("_(", "⚠️", "#")):
            continue
        body = _LIST_MARKER_RE.sub("", sentence)
        if _DATA_RE.search(_BADGE_RE.sub("", body)) and not _BADGE_RE.search(sentence):
            unsupported.append(sentence[:160])
    return GroundingReport(unsupported=unsupported)

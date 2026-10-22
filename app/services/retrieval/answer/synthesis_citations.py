"""Citations built from the evidence ids a synthesized answer cites.

Pulls the cited evidence ids out of the model's bracket markers and maps them
back to ``Citation`` objects with a human-readable document title.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.domain.models.retrieval import EvidenceBundle
from app.domain.models.retrieval.citation import Citation

logger = logging.getLogger(__name__)

_CITE_RE = re.compile(r"\[(?:evidence_id=)?[\"']?([0-9a-fA-F]{32})[\"']?\]")
_HEX_ID_RE = re.compile(r"[0-9a-fA-F]{32}")
_BRACKET_CITE_RE = re.compile(r"\[(?:\s*(?:evidence_id=)?[\"']?[0-9a-fA-F]{32}[\"']?\s*[,;\s]*)+\]")


def _vn_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else text


def format_document_title(
    raw_title: str | None,
    text_content: str | None = None,
    anchors: dict[str, Any] | None = None,
) -> str:
    """Human title for a citation, for any document kind.

    Prefers the document profile (subtype, number, date, title); falls back
    to a printed "Số: …" line, then to a cleaned-up filename.
    """
    anchors = anchors or {}
    profile_title = (anchors.get("profile_title") or "").strip()
    number = anchors.get("doc_number")
    subtype = anchors.get("doc_subtype")
    when = _vn_date(anchors.get("doc_date"))
    if profile_title or number:
        label = profile_title[:120] if profile_title else ""
        if number and number not in label:
            head = f"{subtype or 'Văn bản'} số {number}"
            label = f"{head} – {label}" if label else head
        if when:
            label = f"{label} ({when})"
        return label

    if not raw_title:
        return "Tài liệu nội bộ"

    if text_content:
        so_match = re.search(r"Số\s*:\s*(\d{1,5}\s*/\s*[A-ZĐ][\wĐ\-/]*)", text_content)
        if so_match:
            number = re.sub(r"\s+", "", so_match.group(1))
            return f"Văn bản số {number}"

    cleaned = re.sub(r"\.(?:md|pdf|docx?|xlsx?)$", "", raw_title, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+-\d+-\d+-\d+_", "", cleaned)
    return cleaned.replace("_", " ").strip() or raw_title


def extract_cited_ids(text: str) -> list[str]:
    """Pull unique evidence_id hex strings from single or multi-evidence bracket markers."""
    ids: list[str] = []
    for bracket_match in _BRACKET_CITE_RE.finditer(text):
        for eid in _HEX_ID_RE.findall(bracket_match.group(0)):
            if eid not in ids:
                ids.append(eid)
    return ids


def build_citations_from_bundle(
    cited_ids: list[str],
    bundle: EvidenceBundle,
) -> list[Citation]:
    """Map cited evidence ids back to ``Citation`` objects using bundle items."""
    id_to_item = {item.evidence_id: item for item in bundle.items}
    citations: list[Citation] = []
    seen: set[str] = set()
    for eid in cited_ids:
        item = id_to_item.get(eid)
        if item is None or item.evidence_id in seen:
            if item is None:
                logger.debug("cited_evidence_not_in_bundle", extra={"evidence_id": eid})
            continue
        seen.add(item.evidence_id)
        raw_title = (
            item.title or item.anchors.get("document_title") or item.filename or "Văn bản nội bộ"
        )
        title = format_document_title(raw_title, item.content_raw, item.anchors)
        source_type = item.source_type or item.anchors.get("source_type") or "Văn bản"
        page_start = item.anchors.get("page_start")
        page_end = item.anchors.get("page_end")
        section_title = (
            " > ".join(item.heading_path)
            if item.heading_path
            else item.anchors.get("section_title")
        )
        citations.append(
            Citation(
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                title=title,
                page_start=page_start,
                page_end=page_end,
                heading_path=list(item.heading_path),
                source_type=source_type,
                chunk_id=item.primary_chunk_id or item.evidence_id,
                text=item.content_raw,
                page_number=page_start or 1,
                section_title=section_title,
                score=item.score or item.rerank_score,
                source_uri=item.anchors.get("source_uri") or item.anchors.get("uri"),
                document_version_id=item.document_version_id,
                version_number=item.anchors.get("version_number"),
            )
        )
    return citations

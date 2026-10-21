"""Collect every relation one document version states: rules, signer, LLM."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.config import settings
from app.core.text import unaccent_vietnamese
from app.services.ingestion.relations.llm import extract_llm_relations
from app.services.ingestion.relations.rules import dedupe, extract_rule_relations
from app.services.ingestion.relations.types import RelationDraft, RelationType, TargetKind

# Only administrative documents state legal bases, replacements or awards in
# this phrasing; running the rules on a thesis would only produce noise.
_RELATION_CATEGORIES = {"administrative"}


def signer_relations(entities: Iterable[Mapping[str, Any]]) -> list[RelationDraft]:
    out: list[RelationDraft] = []
    for ent in entities or []:
        name = " ".join(str((ent or {}).get("name") or "").split())
        if (ent or {}).get("kind") != "signer" or not name:
            continue
        role = (ent or {}).get("role")
        out.append(
            RelationDraft(
                relation_type=RelationType.SIGNED_BY,
                target_kind=TargetKind.PERSON,
                target_text=name,
                target_norm=unaccent_vietnamese(name),
                attributes={"role": str(role)} if role else {},
                source="profile",
            )
        )
    return out


_SIGNER_TITLE = re.compile(
    r"^(?:kt\.?\s*)?(?:q\.?\s*)?(?:pho\s+)?(?:tong\s+giam\s+doc|giam\s+doc|chanh\s+van\s+phong|"
    r"truong\s+ban|chu\s+tich|bi\s+thu)$"
)
_SIGNER_NAME = re.compile(r"^([A-ZĐ][a-zà-ỹđ]+(?:\s+[A-ZĐ][a-zà-ỹđ]+){1,4})$")
_SIGNER_LOOKAHEAD = 4


def signer_from_signature_block(text: str) -> RelationDraft | None:
    """The name printed under the signing title ("TỔNG GIÁM ĐỐC" … "Đỗ Tiến Sỹ").

    Fallback for profiles without a signer: the seal image and OCR symbols
    ("★Đỗ Tiên Sỹ") often sit between the title and the name.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if not _SIGNER_TITLE.match(unaccent_vietnamese(line)):
            continue
        for candidate in lines[i + 1 : i + 1 + _SIGNER_LOOKAHEAD]:
            if "<" in candidate or "img" in candidate:
                continue
            name = re.sub(r"^[^A-Za-zÀ-ỹĐđ]+", "", candidate).strip()
            if m := _SIGNER_NAME.match(name):
                return RelationDraft(
                    relation_type=RelationType.SIGNED_BY,
                    target_kind=TargetKind.PERSON,
                    target_text=m.group(1),
                    target_norm=unaccent_vietnamese(m.group(1)),
                    attributes={"role": " ".join(line.split()).title()},
                    evidence=f"{line} … {candidate}",
                    source="rule",
                )
            break
    return None


class DocumentRelationExtractor:
    """Stateless; ``use_llm=False`` keeps extraction deterministic (rules + signer)."""

    def __init__(self, *, use_llm: bool | None = None) -> None:
        self._use_llm = settings.profiling.llm_enabled if use_llm is None else use_llm

    async def aextract(
        self,
        text: str,
        *,
        category: str | None,
        entities: Iterable[Mapping[str, Any]] = (),
    ) -> list[RelationDraft]:
        drafts = signer_relations(entities)
        if category in _RELATION_CATEGORIES and text.strip():
            if not drafts and (signer := signer_from_signature_block(text)):
                drafts.append(signer)
            drafts.extend(extract_rule_relations(text))
            if self._use_llm:
                drafts.extend(await extract_llm_relations(text))
        return dedupe(drafts)

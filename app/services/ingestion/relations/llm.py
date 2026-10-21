"""One LLM call per decision for relations whose targets are people or units.

Award lists and personnel decisions name their targets in free-form lines
("Bà Nguyễn Thuỷ Linh, Phó Bí thư …", "Ban Thư ký biên tập;"), so rules
would be brittle. The reply is validated strictly: unknown types are
dropped, and a target whose name does not occur in the document text is
treated as hallucinated and discarded. Any failure returns ``[]`` so
ingestion never fails because of this step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.core.jsonutil import extract_json_object
from app.core.text import unaccent_vietnamese
from app.services.ingestion.relations.types import RelationDraft, RelationType, TargetKind

logger = logging.getLogger(__name__)

BODY_CHARS = 6000
MAX_RELATIONS = 80
_LLM_TYPES = {
    RelationType.AWARDS,
    RelationType.RECOGNIZES,
    RelationType.APPOINTS,
    RelationType.TRANSFERS,
    RelationType.DISMISSES,
    RelationType.TERMINATES,
    RelationType.RECRUITS,
}
_ATTRIBUTE_KEYS = ("award", "title", "position", "from_position", "to_position", "unit", "reason")
# Only decisions that can contain such relations are worth a call.
_TRIGGER = re.compile(
    r"\b(?:khen|danh hieu|bo nhiem|dieu dong|luan chuyen|thoi giu chuc|mien nhiem|"
    r"cham dut hop dong|tuyen dung|nghi huu)\b"
)
_HONORIFIC = re.compile(r"^(?:ông|bà|anh|chị|đồng\s+chí|đ/c)\s+", re.IGNORECASE)
_DECISION_START = re.compile(r"QUYẾT\s+ĐỊNH\s*:|Điều\s+1\b")

_SYSTEM = (
    "Bạn trích quan hệ từ một quyết định hành chính tiếng Việt. Chỉ dùng thông tin có trong "
    "văn bản, chép nguyên văn tên người/đơn vị. Không suy đoán. Chỉ trả về một đối tượng JSON."
)
_SCHEMA = {
    "relations": [
        {
            "type": "awards|recognizes|appoints|transfers|dismisses|terminates|recruits",
            "target": "tên người (bỏ Ông/Bà) hoặc tên tập thể/đơn vị, nguyên văn",
            "target_kind": "person|organization",
            "award": "hình thức khen thưởng (Bằng khen của …) | null",
            "title": "danh hiệu được công nhận (Chiến sĩ thi đua cơ sở …) | null",
            "position": "chức vụ/đơn vị công tác hiện tại của người đó | null",
            "from_position": "chức vụ/đơn vị cũ (điều động, thôi giữ chức) | null",
            "to_position": "chức vụ/đơn vị mới (bổ nhiệm, điều động, tuyển dụng) | null",
            "reason": "thành tích/lý do, ngắn gọn | null",
        }
    ]
}
_GUIDE = (
    "Loại quan hệ:\n"
    "- awards: tặng Bằng khen/Giấy khen/khen thưởng cho tập thể hoặc cá nhân (mỗi đối tượng một dòng)\n"
    "- recognizes: công nhận danh hiệu thi đua (Chiến sĩ thi đua, Lao động tiên tiến…)\n"
    "- appoints: bổ nhiệm hoặc bổ nhiệm lại\n"
    "- transfers: điều động, luân chuyển\n"
    "- dismisses: cho thôi giữ chức vụ, miễn nhiệm\n"
    "- terminates: chấm dứt hợp đồng, nghỉ hưu\n"
    "- recruits: tuyển dụng\n"
    "Không liệt kê thành viên hội đồng, tổ công tác hay nơi nhận. Không có quan hệ nào thì trả "
    '{"relations": []}.'
)


def needs_llm(text: str) -> bool:
    return bool(_TRIGGER.search(unaccent_vietnamese(text)))


def decision_body(text: str) -> str:
    m = _DECISION_START.search(text)
    return (text[m.start() :] if m else text)[:BODY_CHARS]


def _prompt(body: str) -> str:
    return (
        f"{_GUIDE}\n\nTrả về JSON theo khuôn: {json.dumps(_SCHEMA, ensure_ascii=False)}\n\n"
        f"--- VĂN BẢN ---\n{body}\n"
    )


def parse_llm_relations(reply: dict[str, Any] | None, document_text: str) -> list[RelationDraft]:
    """Validate the model's JSON; keep only targets that occur in the document."""
    if not isinstance(reply, dict):
        return []
    folded_doc = unaccent_vietnamese(document_text)
    out: list[RelationDraft] = []
    for item in (reply.get("relations") or [])[:MAX_RELATIONS]:
        if not isinstance(item, dict):
            continue
        try:
            relation = RelationType(str(item.get("type") or "").strip().lower())
        except ValueError:
            continue
        if relation not in _LLM_TYPES:
            continue
        target = _HONORIFIC.sub("", " ".join(str(item.get("target") or "").split())).strip(" ;,.")
        norm = unaccent_vietnamese(target)
        if not target or len(target) > 160 or norm not in folded_doc:
            continue
        kind = (
            TargetKind.ORGANIZATION
            if str(item.get("target_kind")) == "organization"
            else TargetKind.PERSON
        )
        attrs = {
            k: " ".join(str(item[k]).split())[:200]
            for k in _ATTRIBUTE_KEYS
            if item.get(k) not in (None, "", "null")
        }
        out.append(
            RelationDraft(
                relation_type=relation,
                target_kind=kind,
                target_text=target,
                target_norm=norm,
                attributes=attrs,
                source="llm",
            )
        )
    return out


async def extract_llm_relations(text: str) -> list[RelationDraft]:
    api_key = str(settings.llm.openai_api_key or "")
    if not api_key or not needs_llm(text):
        return []
    timeout = min(settings.timeouts.llm_request_seconds, settings.profiling.llm_timeout_seconds)
    payload = {
        "model": settings.llm.primary_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(decision_body(text))},
        ],
        "temperature": 0.0,
        # 9router answers with SSE unless told otherwise.
        "stream": False,
    }
    try:
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client,
        ):
            resp = await client.post(
                settings.llm.chat_completions_url(),
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
        return parse_llm_relations(extract_json_object(content), text)
    except Exception as exc:  # noqa: BLE001 - relation extraction must never fail ingestion
        logger.warning("relation_llm_extraction_failed error=%s", exc)
        return []

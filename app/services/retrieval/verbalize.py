"""Phrase a structured (SQL) answer as natural prose without changing its facts.

The relation and metadata routes answer from SQL: the facts are exact but
the deterministic text reads like a report dump ("Tìm thấy 1 văn bản theo
quan hệ đã lưu …"). One short LLM call rewrites that draft as a direct
answer to the question; the rewrite is accepted only when it

* keeps every citation label of the draft and invents none,
* contains no figure (number, date, document number) absent from the draft,
* stays within a sane length.

Otherwise — or when the gateway is down — the deterministic draft is used,
so this step can only change the wording, never the facts.
"""

from __future__ import annotations

import logging
import re

from app.services.documents.llm import llm_text
from app.services.documents.quality import _number_values, _significant

logger = logging.getLogger(__name__)

PHRASE_TIMEOUT_SECONDS = 25.0

PHRASE_SYSTEM_PROMPT = (
    "Bạn là trợ lý tra cứu văn bản nội bộ. Bạn nhận một câu hỏi và BẢN NHÁP câu trả lời "
    "đã được tra chính xác từ cơ sở dữ liệu. Hãy viết lại thành câu trả lời tự nhiên, "
    "trả lời thẳng vào câu hỏi bằng tiếng Việt.\n"
    "1. Chỉ dùng thông tin có trong bản nháp; không thêm, không suy đoán, không bỏ văn bản "
    "nào. Giữ NGUYÊN VĂN số hiệu, ngày tháng, tên người, tên đơn vị, chức vụ, số lượng.\n"
    "2. Giữ mọi nhãn trích dẫn [1], [2], … ngay sau văn bản tương ứng; không tạo nhãn mới.\n"
    "3. Mở đầu bằng câu trả lời trực tiếp (ví dụ: 'Trong kho có một quyết định bổ nhiệm: …'). "
    "Một đến ba văn bản thì viết thành đoạn văn; từ bốn văn bản trở lên thì dùng danh sách "
    "gạch đầu dòng ngắn, mỗi dòng một văn bản.\n"
    "4. Không dùng các cụm như 'theo quan hệ đã lưu', 'trong cơ sở dữ liệu', 'bản nháp', "
    "'dữ kiện'; không chào hỏi, không đặt câu hỏi lại cho người dùng.\n"
    "Chỉ trả về câu trả lời."
)

_LABEL = re.compile(r"\[(\d+)\]")
_DOC_NUMBER = re.compile(r"\b\d{1,5}\s*[/-]\s*[A-ZĐ][\w/-]*", re.UNICODE)
_BANNED = re.compile(r"(?i)theo quan hệ đã lưu|bản nháp|cơ sở dữ liệu|dữ kiện")


def accept_phrasing(draft: str, text: str) -> bool:
    """True when ``text`` says nothing the draft does not, and drops no citation."""
    if not text or len(text) > max(400, int(len(draft) * 2.5)):
        return False
    if set(_LABEL.findall(text)) != set(_LABEL.findall(draft)):
        return False
    if _significant(_number_values(_LABEL.sub("", text))) - _number_values(_LABEL.sub("", draft)):
        return False

    def numbers(s: str) -> set[str]:
        return {re.sub(r"\s+", "", m) for m in _DOC_NUMBER.findall(s)}

    if numbers(text) - numbers(draft):
        return False
    return not _BANNED.search(text)


async def phrase_structured_answer(question: str, draft: str) -> str:
    """Natural wording for a SQL-backed answer; the draft itself when unsure."""
    if not draft.strip():
        return draft
    user = f"Câu hỏi: {question}\n\nBẢN NHÁP:\n{draft}"
    text = await llm_text(PHRASE_SYSTEM_PROMPT, user, timeout=PHRASE_TIMEOUT_SECONDS)
    text = (text or "").strip()
    if accept_phrasing(draft, text):
        return text
    if text:
        logger.info("structured_phrasing_rejected chars=%d", len(text))
    return draft

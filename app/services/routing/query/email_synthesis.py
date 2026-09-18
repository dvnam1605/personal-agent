"""Email summarization and draft composition helpers using heuristics and LLM."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.core.config import settings
from app.services.skills.matching import unaccent_vietnamese

logger = logging.getLogger(__name__)


def fallback_summarize_emails(query: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic, structured summary when LLM is offline or times out."""
    total = len(messages)
    unread_count = sum(1 for m in messages if m.get("unread"))

    critical: list[dict[str, Any]] = []
    work: list[dict[str, Any]] = []
    news: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []

    _WORK_KEYWORDS = (
        "linkedin",
        "job",
        "career",
        "recruitment",
        "tuyen dung",
        "ung tuyen",
        "phong van",
        "interview",
        "hr",
        "offer",
        "hiring",
        "developer",
        "engineer",
    )
    _CRITICAL_KEYWORDS = (
        "canh bao",
        "bao mat",
        "security",
        "otp",
        "xac minh",
        "xac thuc",
        "ma dung mot lan",
        "mat ma",
        "ngan hang",
        "bank",
        "vpbank",
        "vietcombank",
        "techcombank",
        "giao dich",
        "transfer",
        "thanh toan",
        "payment",
        "invoice",
        "hoa don",
    )
    _NEWS_KEYWORDS = (
        "medium",
        "digest",
        "newsletter",
        "youtube",
        "dang ky",
        "hoi vien",
        "khuyen mai",
        "quang cao",
        "promo",
        "daily",
    )

    for m in messages:
        text = unaccent_vietnamese(
            f"{m.get('from', '')} {m.get('subject', '')} {m.get('snippet', '')}"
        ).casefold()

        # Check work first so "jobalerts" doesn't collide with security "alert"
        if any(k in text for k in _WORK_KEYWORDS):
            work.append(m)
        elif any(k in text for k in _CRITICAL_KEYWORDS):
            critical.append(m)
        elif any(k in text for k in _NEWS_KEYWORDS):
            news.append(m)
        else:
            others.append(m)

    sections = []
    unread_note = f" ({unread_count} email chưa đọc)" if unread_count else ""
    sections.append(f"📌 **Tổng quan**: Tìm thấy {total} email trong hộp thư{unread_note}.")

    def _fmt_item(m: dict[str, Any]) -> str:
        sender = m.get("from") or m.get("from_email") or "Không rõ người gửi"
        subject = m.get("subject") or "(Không có tiêu đề)"
        snippet = m.get("snippet", "").strip()
        snippet_text = f' — *"{snippet[:120]}..."*' if snippet else ""
        return f"- **{subject}** ({sender}){snippet_text}"

    if critical:
        sections.append(
            "🔴 **Quan trọng / Cần lưu ý ngay**:\n" + "\n".join(_fmt_item(m) for m in critical)
        )
    if work:
        sections.append("💼 **Công việc & Tuyển dụng**:\n" + "\n".join(_fmt_item(m) for m in work))
    if news:
        sections.append(
            "📰 **Bản tin & Thông báo dịch vụ**:\n" + "\n".join(_fmt_item(m) for m in news)
        )
    if others:
        sections.append("✉️ **Email khác**:\n" + "\n".join(_fmt_item(m) for m in others))

    return "\n\n".join(sections)


async def summarize_emails_stream(
    query: str,
    messages: list[dict[str, Any]],
    *,
    timeout_seconds: float = 45.0,
) -> AsyncGenerator[str, None]:
    """Stream summarize inbox messages token-by-token directly from LLM."""
    if not messages:
        yield "Không có email nào khớp trong hộp thư đến."
        return

    try:
        api_key = settings.llm.openai_api_key
        if api_key:
            system_prompt = (
                "Bạn là Namm Agent - trợ lý điều hành AI chuyên nghiệp.\n"
                "Nhiệm vụ: Đọc nội dung email rồi viết BÁO CÁO TÓM TẮT tự nhiên bằng tiếng Việt.\n\n"
                "Quy tắc bắt buộc:\n"
                "- KHÔNG dùng heading markdown (# ## ###). Chỉ dùng emoji + **in đậm** làm đề mục.\n"
                "- KHÔNG lặp lại nguyên văn tiêu đề email; phải đọc snippet để tóm tắt giá trị thực.\n"
                "- Viết tự nhiên như đang nói chuyện với chủ nhân hộp thư, ngắn gọn mà đủ ý.\n\n"
                "Cấu trúc phản hồi (đúng thứ tự):\n\n"
                "📌 **Tổng quan nhanh**\n"
                "1-2 câu tóm gọn: có bao nhiêu email, điểm đáng chú ý nhất là gì.\n\n"
                "🔴 **Quan trọng / Cần lưu ý ngay**\n"
                "Liệt kê dạng bullet, mỗi item viết rõ hành động hoặc thông tin cốt lõi "
                "(bảo mật tài khoản, mã OTP, giao dịch ngân hàng...).\n\n"
                "💼 **Công việc & Tuyển dụng**\n"
                "Cơ hội việc làm, thông tin đồng nghiệp/đối tác (nếu có).\n\n"
                "📰 **Bản tin & Khác**\n"
                "Bài viết, thông báo dịch vụ, quà tặng... (nếu có).\n\n"
                "Bỏ qua mục nào không có email phù hợp. Không thêm lời kết thừa."
            )

            email_entries = []
            for i, item in enumerate(messages, 1):
                sender = item.get("from") or item.get("from_email") or "Không rõ người gửi"
                subject = item.get("subject") or "(Không có tiêu đề)"
                when = item.get("when") or ""
                status = "Chưa đọc" if item.get("unread") else "Đã đọc"
                snippet = item.get("snippet") or ""
                email_entries.append(
                    f"{i}. Từ: {sender}\n"
                    f"   Tiêu đề: {subject}\n"
                    f"   Thời gian: {when} ({status})\n"
                    f"   Trích đoạn nội dung: {snippet}"
                )

            user_content = (
                f"Yêu cầu của người dùng: {query}\n\n"
                f"Danh sách {len(messages)} email nhận được:\n\n" + "\n\n".join(email_entries)
            )

            model = settings.llm.fast_model
            has_yielded = False
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    settings.llm.chat_completions_url(),
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.2,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            payload = json.loads(data_str)
                            choices = payload.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta", {}).get("content")
                                if delta:
                                    has_yielded = True
                                    yield delta
                        except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                            continue

            if has_yielded:
                return
    except Exception:  # noqa: BLE001 - LLM is optional; fall back to heuristic summary
        logger.exception("llm_email_summarization_failed")

    yield fallback_summarize_emails(query, messages)


async def summarize_emails(
    query: str,
    messages: list[dict[str, Any]],
    *,
    timeout_seconds: float = 45.0,
) -> str:
    """Summarize inbox messages using LLM when available, falling back to heuristic categorization."""
    tokens = []
    async for t in summarize_emails_stream(query, messages, timeout_seconds=timeout_seconds):
        tokens.append(t)
    return "".join(tokens)


async def compose_email_draft(query: str) -> tuple[str, str, list[str]]:
    """Generate subject, body, and recipients for an email draft request using LLM."""
    subject = "Xin nghỉ phép việc gia đình"
    recipients = ["quanly@vov.vn"]
    body = (
        "Kính gửi: Quản lý trực tiếp,\n\n"
        "Tôi viết email này để xin phép được nghỉ làm việc 01 ngày vì lý do bận việc gia đình cần trực tiếp giải quyết.\n\n"
        "Về tiến độ công việc, tôi đã sắp xếp và bàn giao các nhiệm vụ phát sinh cho đồng nghiệp hỗ trợ theo dõi. "
        "Trong ngày nghỉ, tôi vẫn sẽ kiểm tra email định kỳ và có thể liên hệ qua điện thoại nếu có vấn đề khẩn cấp.\n\n"
        "Rất mong nhận được sự thông cảm và phê duyệt từ Quản lý.\n\n"
        "Trân trọng,\n"
        "[Tên của bạn]"
    )

    try:
        api_key = settings.llm.openai_api_key
        if api_key:
            system_prompt = (
                "Bạn là Namm Agent - trợ lý điều hành AI chuyên nghiệp.\n"
                "Nhiệm vụ: Soạn thảo một email công việc chuyên nghiệp, lịch sự bằng tiếng Việt theo yêu cầu của người dùng.\n\n"
                "Quy tắc phản hồi:\n"
                "Chỉ trả về DUY NHẤT một khối JSON hợp lệ theo cấu trúc sau, không kèm bất kỳ lời giải thích nào khác:\n"
                "{\n"
                '  "subject": "Tiêu đề email ngắn gọn, chuẩn mực",\n'
                '  "recipients": ["địa_chỉ_email_hoặc_tên_người_nhận"],\n'
                '  "body": "Nội dung đầy đủ bức email (chào hỏi kính gửi, lý do, chi tiết công việc/bàn giao, lời kết, ký tên)"\n'
                "}\n"
            )
            user_content = f"Yêu cầu: {query}"
            model = settings.llm.fast_model or settings.llm.primary_model
            timeout = 35.0
            chunks: list[str] = []

            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    settings.llm.chat_completions_url(),
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.3,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            payload = json.loads(data_str)
                            choices = payload.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta", {}).get("content")
                                if delta:
                                    chunks.append(delta)
                        except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                            continue

            raw = "".join(chunks).strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                sub = parsed.get("subject")
                b = parsed.get("body")
                rec = parsed.get("recipients")
                if sub and b:
                    subject = str(sub).strip()
                    body = str(b).strip()
                    if isinstance(rec, list) and rec:
                        recipients = [str(r).strip() for r in rec if str(r).strip()]
                    elif isinstance(rec, str) and rec.strip():
                        recipients = [rec.strip()]
    except Exception:  # noqa: BLE001 - LLM draft is optional
        logger.exception("compose_email_draft_llm_failed")

    return subject, body, recipients


__all__ = [
    "compose_email_draft",
    "fallback_summarize_emails",
    "summarize_emails",
    "summarize_emails_stream",
]

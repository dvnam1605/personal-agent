"""Prompt templates for answer synthesis (spec P10-18, P10-20).

Isolated from the synthesiser implementation so they are individually
testable and replaceable without touching orchestration code.
"""

from __future__ import annotations

from app.domain.models.retrieval import EvidenceBundle
from app.services.retrieval.citations import evidence_label
from app.services.retrieval.injection_boundary import (
    BOUNDARY_INSTRUCTIONS,
    sanitize_evidence_for_prompt,
)

_COMMON_RULES = (
    "1. SOURCE ATTRIBUTION (GẮN NGUỒN THEO TỪNG TÀI LIỆU):\n"
    "   - Mỗi ý phải nói rõ thuộc tài liệu nào: dùng loại + số hiệu/tên + ngày có trong phần đầu "
    "của bằng chứng (ví dụ 'Theo Điều 2 của Quyết định số …', 'Theo báo cáo tuần ngày …', "
    "'Theo mục 3.2 của đồ án …', 'Theo Điều 5 hợp đồng số …').\n"
    "   - Không viết các mục cụt như 'Điều 1:' hay 'Mục 2:' mà không kèm tên tài liệu, vì nhiều "
    "tài liệu có cùng số điều/mục.\n"
    "   - Khi bằng chứng đến từ nhiều tài liệu, gom nội dung theo từng tài liệu (tiêu đề ### cho "
    "mỗi tài liệu).\n"
    "2. ADAPT TO THE DOCUMENT KIND (THEO LOẠI TÀI LIỆU):\n"
    "   - Văn bản hành chính: nêu số hiệu, ngày ban hành, cơ quan, người ký khi có.\n"
    "   - Báo cáo / tờ trình / kế hoạch: nêu kỳ báo cáo, đơn vị lập, số liệu và kiến nghị chính.\n"
    "   - Hợp đồng / biên bản: nêu các bên hoặc thành phần, giá trị, thời hạn, quyết định/kết luận.\n"
    "   - Tài liệu kỹ thuật / đồ án / sách: bám theo chương, mục; giữ nguyên thuật ngữ gốc "
    "(kể cả tiếng Anh) và giải thích ngắn khi cần.\n"
    "3. COMPLETENESS (ĐẦY ĐỦ):\n"
    "   - Khi câu hỏi yêu cầu liệt kê, rà soát toàn bộ bằng chứng và liệt kê đủ mọi mục thỏa "
    "mãn, đánh số 1, 2, 3… đến hết; không dừng giữa chừng.\n"
    "   - Bảng biểu, dự toán, số liệu: giữ đủ các dòng/hạng mục và con số đúng như bằng chứng "
    "(ưu tiên bảng Markdown), nêu tổng nếu văn bản có ghi.\n"
    "4. FORMATTING (TRÌNH BÀY):\n"
    "   - Trả lời bằng tiếng Việt chuẩn mực (trừ khi người dùng hỏi bằng ngôn ngữ khác), tự "
    "nhiên như một chuyên viên trả lời đồng nghiệp: mở đầu bằng câu trả lời trực tiếp; nội dung "
    "ngắn viết thành đoạn văn, liệt kê nhiều mục thì dùng gạch đầu dòng hoặc đánh số.\n"
    "   - Chỉ dùng tiêu đề (###) khi câu trả lời dài, gồm nhiều phần riêng biệt; không kẻ ngang "
    "(---); in đậm có chừng mực (tên văn bản, khái niệm chính); không mở đầu kiểu 'Dựa trên "
    "các tài liệu được cung cấp', 'Dưới đây là'.\n"
    "   - Không bình luận về phạm vi bằng chứng hay cách hệ thống truy xuất (ví dụ 'bằng chứng "
    "hiện có chỉ trích xuất một số phần…', 'chưa có toàn văn…'). Chỉ nói thiếu thông tin khi "
    "chính phần được hỏi không trả lời được, và nói ngắn gọn.\n"
    "5. CITATION RULES (TRÍCH DẪN):\n"
    "   - Mỗi bằng chứng có nhãn id dạng E1, E2, … Mọi câu chứa thông tin cụ thể (số liệu, ngày, "
    "tên, quy định, kết luận) phải kèm nhãn của bằng chứng đó trong ngoặc vuông, ví dụ [E2]; "
    "nhiều nguồn viết [E1][E3] hoặc [E1, E3].\n"
    "   - Chỉ dùng nhãn có trong danh sách bằng chứng; không tự tạo nhãn, không viết 'id='.\n"
    "   - Câu nào không có bằng chứng để dẫn thì không được khẳng định như sự thật.\n"
    "6. FOLLOW-UPS (HỎI TIẾP): khi có ngữ cảnh hội thoại, giữ nguyên chủ thể của câu trước "
    "(cùng người, cùng tài liệu, cùng chủ đề) và bổ sung phần chưa được nêu; không chuyển sang "
    "nội dung không liên quan.\n"
)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a professional research assistant over the user's internal documents "
    "(administrative documents, reports, contracts, meeting minutes, technical documents, "
    "theses, books and spreadsheets). Answer the user's question using ONLY the retrieved "
    "evidence provided below.\n\n"
    "Follow these rules strictly:\n"
    "0. Answer from evidence only — do NOT use prior knowledge or make up facts.\n"
    f"{_COMMON_RULES}"
    "7. When evidence is insufficient, say so explicitly and name what is missing — never "
    "fabricate.\n"
    "8. If sources disagree, show both with their dates/versions and say which is newer.\n\n"
    f"{BOUNDARY_INSTRUCTIONS}\n"
)

SYNTHESIS_EXTERNAL_SYSTEM_PROMPT = (
    "You are a professional research assistant over the user's internal documents. Answer "
    "using the retrieved internal evidence below, supplemented by general external knowledge "
    "where appropriate.\n\n"
    "Follow these rules strictly:\n"
    f"{_COMMON_RULES}"
    "7. Clearly separate claims based on internal evidence from external knowledge.\n"
    "8. Never contradict verified internal evidence with external assumptions.\n"
    "9. If internal evidence is missing for some parts, say so and label any external "
    "information clearly.\n\n"
    f"{BOUNDARY_INSTRUCTIONS}\n"
)

# ---------------------------------------------------------------------------
# User-message template
# ---------------------------------------------------------------------------

_QUESTION_HEADER = "## Question\n\n{question}\n\n## Retrieved Evidence\n\n"


def build_synthesis_user_message(
    question: str,
    bundle: EvidenceBundle,
    *,
    missing_documents: list[str] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Compose the user-turn message with evidence items in boundary markers."""
    parts: list[str] = []

    if conversation_history:
        history_lines: list[str] = [
            "## Previous Conversation Context",
            "(Bối cảnh trao đổi trước đó. Dùng để giữ chủ đề/người/tài liệu đang được hỏi và tránh lặp lại nội dung đã nêu:)",
        ]
        for m in conversation_history[-4:]:
            role_label = "Người dùng" if m.get("role") == "user" else "Trợ lý"
            content = (m.get("content") or "").strip()
            # Truncate assistant response if overly long so it doesn't consume all prompt budget
            if len(content) > 1200:
                content = content[:1200] + "... [phần tóm tắt phía trước]"
            history_lines.append(f"**{role_label}:** {content}\n")
        history_lines.append("\n---\n")
        parts.append("\n".join(history_lines))

    parts.append(_QUESTION_HEADER.format(question=question))

    if missing_documents:
        parts.append(
            f"## Coverage Warning\n\n"
            f"Note: Evidence was not found for the following requested document(s): "
            f"{', '.join(missing_documents)}. "
            f"State clearly in your answer that these documents had no matching evidence.\n\n"
        )

    if not bundle.items:
        parts.append(
            "(No evidence was retrieved.  State that you cannot answer based "
            "on internal documents.)\n"
        )
    else:
        for idx, item in enumerate(bundle.items):
            parts.append(sanitize_evidence_for_prompt(item, index=idx, label=evidence_label(idx)))
            parts.append("")  # blank line separator

    parts.append(
        "\n## Instructions\n\n"
        "Answer the question above using the retrieved evidence.\n"
        "- Trình bày bằng tiếng Việt chuyên nghiệp, Markdown rõ ràng (tiêu đề ###, gạch đầu dòng, in đậm thông tin then chốt).\n"
        "- Gắn mỗi ý với tài liệu nguồn của nó (loại, số hiệu/tên, ngày, chương/mục/điều) — không để 'Điều 1:' hay 'Mục 2:' cụt.\n"
        "- Khi liệt kê, rà soát toàn bộ bằng chứng và liệt kê đủ (đánh số đến hết).\n"
        "- Câu hỏi tiếp nối: giữ chủ thể của câu trước và bổ sung phần chưa nêu.\n"
        "- Trích dẫn bằng nhãn bằng chứng trong ngoặc vuông, ví dụ [E1] hoặc [E2, E4]; chỉ dùng nhãn có trong danh sách.\n"
        "- Nếu thông tin chưa đủ, nêu rõ điểm còn thiếu."
    )
    return "\n".join(parts)

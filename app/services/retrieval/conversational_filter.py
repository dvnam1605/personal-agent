"""Universal Vietnamese conversational, interrogative, and polite filler filtering.

Provides language-level cleaning of Vietnamese conversational search queries,
removing conversational noise, question endings, and polite phrases,
while preserving domain keywords, proper names, and entity identifiers.
"""

from __future__ import annotations

import re
from app.services.retrieval.vietnamese_orthography import normalize_unicode

# Common typos and accents in Vietnamese search queries (Universal)
UNIVERSAL_TYPOS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdựa\s+toán\b", re.IGNORECASE), "dự toán"),
    (re.compile(r"\bdua\s+toan\b", re.IGNORECASE), "dự toán"),
    (re.compile(r"\bkinh\s+phi\b", re.IGNORECASE), "kinh phí"),
    (re.compile(r"\bbang\s+khen\b", re.IGNORECASE), "bằng khen"),
    (re.compile(r"\bkhen\s+thuong\b", re.IGNORECASE), "khen thưởng"),
    (re.compile(r"\bquyet\s+dinh\b", re.IGNORECASE), "quyết định"),
    (re.compile(r"\btong\s+giam\s+doc\b", re.IGNORECASE), "tổng giám đốc"),
    (re.compile(r"\bpho\s+tong\s+giam\s+doc\b", re.IGNORECASE), "phó tổng giám đốc"),
]

# Polite requests and exploratory prefixes
_POLITE_PREFIXES = re.compile(
    r"^(?:cho\s+tôi\s+biết|cho\s+biết|hãy\s+cho\s+biết|hãy\s+tìm|tìm\s+cho\s+tôi|tìm\s+kiếm|"
    r"tra\s+cứu|cho\s+tôi\s+xem|cho\s+xem|vui\s+lòng\s+cho\s+biết|vui\s+lòng|làm\s+ơn|"
    r"xin\s+hỏi|tôi\s+muốn\s+hỏi|tôi\s+muốn\s+biết|tôi\s+cần\s+tìm|cần\s+tìm)\s+",
    re.IGNORECASE,
)

# Interrogative suffixes & fillers
_INTERROGATIVE_FILLERS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:là\s+ai|là\s+gì|ở\s+đâu|khi\s+nào)\b", re.IGNORECASE),
    re.compile(r"\b(?:bao\s+nhiêu\s+tiền|là\s+bao\s+nhiêu|bao\s+nhiêu|hết\s+bao\s+nhiêu)\b", re.IGNORECASE),
    re.compile(r"\b(?:như\s+thế\s+nào|như\s+nào|thế\s+nào|ra\s+sao)\b", re.IGNORECASE),
    re.compile(r"\b(?:thông\s+tin\s+về|chi\s+tiết\s+về|nội\s+dung\s+về|hỏi\s+về)\b", re.IGNORECASE),
    re.compile(r"\b(?:đã\s+có|có\s+những|những\s+ai|ai\s+được|được\s+không|giúp\s+tôi|có\s+gì|có\s+không)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:đã\s+kí\s+những|đã\s+ký\s+những|đã\s+kí|đã\s+ký|kí\s+những|ky\s+những|"
        r"đã\s+ban\s+hành|ban\s+hành|ký\s+duyệt|phê\s+duyệt)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:những\s+văn\s+bản\s+nào|văn\s+bản\s+nào|quyết\s+định\s+nào|tài\s+liệu\s+nào|"
        r"văn\s+bản\s+gì|quyết\s+định\s+gì|tài\s+liệu\s+gì|những\s+gì\s+nữa)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:còn\s+văn\s+bản\s+nào\s+nữa\s+không|còn\s+văn\s+bản\s+nào\s+không|còn\s+tài\s+liệu\s+nào\s+nữa\s+không|"
        r"còn\s+không|còn\s+gì\s+không|còn\s+ai\s+nữa\s+không|còn\s+nữa\s+không|ngoài\s+ra\s+còn)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:liệt\s+kê|danh\s+sách|tất\s+cả|toàn\s+bộ|tổng\s+hợp)\b", re.IGNORECASE),
    re.compile(r"\b(?:do\s+ai\s+ký|do\s+ai\s+kí|ai\s+ký|ai\s+kí|ai\s+ban\s+hành)\b", re.IGNORECASE),
    re.compile(r"\b(?:ông|bà|đồng\s+chí|đ/c)\b", re.IGNORECASE),
]

# Honorific titles and roles for dynamic entity extraction
HONORIFIC_NAME_REGEX = re.compile(
    r"\b(?:ông|bà|đồng\s+chí|đ/c|tiến\s+sĩ|ts|thạc\s+sĩ|th\.s|giáo\s+sư|gs|tổng\s+giám\s+đốc|tgđ|giám\s+đốc|chủ\s+tịch|bộ\s+trưởng)\s+"
    r"([A-ZÀ-Ỹa-zà-ỹ\s]{3,35}?)"
    r"(?=(?:\s+(?:đã|ký|kí|ban\s+hành|chỉ\s+đạo|cho\s+biết|chịu\s+trách\s+nhiệm|là\s+ai|có|năm)\b|[?!.,]|\s*$))",
    re.IGNORECASE,
)


# Patterns that signal a multi-turn follow-up question
FOLLOWUP_PATTERNS = [
    re.compile(r"\b(?:còn\s+văn\s+bản\s+nào|còn\s+tài\s+liệu\s+nào|còn\s+quyết\s+định\s+nào)\b", re.IGNORECASE),
    re.compile(r"\b(?:còn\s+nữa\s+không|còn\s+gì\s+nữa|còn\s+ai\s+nữa|tiếp\s+tục|ngoài\s+ra)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:còn\s+nữa\s+ko|còn\s+nữa\s+không|còn\s+ko|còn\s+không)\s*\??\s*$", re.IGNORECASE),
]

# Patterns that signal an inventory / listing intent (retrieving all matching items)
LISTING_PATTERNS = [
    re.compile(r"\b(?:những\s+văn\s+bản\s+nào|các\s+văn\s+bản\s+nào|những\s+quyết\s+định\s+nào)\b", re.IGNORECASE),
    re.compile(r"\b(?:đã\s+kí|đã\s+ký|kí\s+những|ký\s+những|ai\s+ký|ai\s+kí|do\s+ai\s+ký)\b", re.IGNORECASE),
    re.compile(r"\b(?:danh\s+sách|liệt\s+kê|tất\s+cả|toàn\s+bộ|tổng\s+hợp)\b", re.IGNORECASE),
    re.compile(r"\b(?:còn\s+văn\s+bản\s+nào|còn\s+gì\s+nữa\s+không|còn\s+nữa\s+không)\b", re.IGNORECASE),
]


def clean_conversational_phrasing(query: str) -> str:
    """Strip conversational filler, polite prefixes, and interrogative markers.

    Returns the substantive semantic keywords suitable for lexical and dense search.
    """
    cleaned = normalize_unicode(query)

    # 1. Apply typos
    for pat, rep in UNIVERSAL_TYPOS:
        cleaned = pat.sub(rep, cleaned)

    # 2. Strip polite prefixes
    cleaned = _POLITE_PREFIXES.sub("", cleaned)

    # 3. Strip question endings and conversational fillers
    for pat in _INTERROGATIVE_FILLERS:
        cleaned = pat.sub(" ", cleaned)

    # 4. Remove punctuation marks
    cleaned = re.sub(r"[?!.,:;\"'()\[\]{}]+", " ", cleaned)

    # 5. Clean whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_potential_honorific_names(query: str) -> list[str]:
    """Extract person names preceded by honorifics or titles (e.g. 'ông Đỗ Tiến Sỹ' -> 'Đỗ Tiến Sỹ')."""
    normalized = normalize_unicode(query)
    names: list[str] = []
    for match in HONORIFIC_NAME_REGEX.finditer(normalized):
        raw_name = match.group(1).strip()
        # Filter out obvious false positives
        words = raw_name.split()
        if 1 < len(words) <= 5 and not any(w.lower() in ("văn", "bản", "gì", "nào", "không") for w in words[:1]):
            names.append(raw_name)
    return names


def is_listing_intent(query: str) -> bool:
    """Return True if the query asks to enumerate, list, or check signed documents."""
    normalized = normalize_unicode(query)
    return any(pat.search(normalized) for pat in LISTING_PATTERNS)


def is_followup_intent(query: str) -> bool:
    """Return True if the query is a multi-turn follow-up continuing the prior conversation."""
    normalized = normalize_unicode(query)
    return any(pat.search(normalized) for pat in FOLLOWUP_PATTERNS)

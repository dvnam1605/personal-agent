"""Query reformulation and normalization for Vietnamese RAG retrieval.

Provides deterministic query cleaning, typo correction, conversational stopword removal,
and compound phrase extraction for PostgreSQL FTS websearch_to_tsquery.
"""

from __future__ import annotations

import re

# Common typos in Vietnamese search queries
TYPO_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdựa\s+toán\b", re.IGNORECASE), "dự toán"),
    (re.compile(r"\bdua\s+toan\b", re.IGNORECASE), "dự toán"),
    (re.compile(r"\bkinh\s+phi\b", re.IGNORECASE), "kinh phí"),
    (re.compile(r"\bbang\s+khen\b", re.IGNORECASE), "bằng khen"),
    (re.compile(r"\bkhen\s+thuong\b", re.IGNORECASE), "khen thưởng"),
    (re.compile(r"\bquyet\s+dinh\b", re.IGNORECASE), "quyết định"),
    (re.compile(r"\btong\s+giam\s+doc\b", re.IGNORECASE), "tổng giám đốc"),
    (re.compile(r"\bdai\s+tieng\s+noi\b", re.IGNORECASE), "đài tiếng nói"),
]

# Conversational question filler patterns to remove from lexical search
CONVERSATIONAL_FILLERS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(la\s+bao\s+nhieu|bao\s+nhieu\s+tien|bao\s+nhieu|het\s+bao\s+nhieu)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(nhu\s+the\s+nao|nhu\s+nao|the\s+nao|ra\s+sao)\b", re.IGNORECASE),
    re.compile(
        r"\b(cho\s+toi\s+biet|cho\s+biet|hay\s+cho\s+biet|hay\s+tim|tim\s+cho\s+toi|tim\s+kiem|tra\s+cuu)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(thong\s+tin\s+ve|chi\s+tiet\s+ve|noi\s+dung\s+ve|hoi\s+ve)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(da\s+co|co\s+nhung|nhung\s+ai|ai\s+duoc|duoc\s+khong|giup\s+toi|co\s+gi|co\s+khong)\b",
        re.IGNORECASE,
    ),
]

# Key compound phrases in VOV administrative documents for high-precision FTS OR-matching
KEY_PHRASES: list[str] = [
    "dự toán kinh phí",
    "dự toán",
    "kinh phí",
    "ngân sách",
    "tặng bằng khen",
    "khen thưởng",
    "bằng khen",
    "thi đua",
    "chiến sĩ thi đua",
    "tiết kiệm chống lãng phí",
    "thông tin khoa học",
    "nghiên cứu khoa học",
    "khoa học và công nghệ",
    "tiền lương",
    "phụ cấp",
    "bổ nhiệm",
    "tổng giám đốc",
    "phó tổng giám đốc",
    "đài tiếng nói việt nam",
    "đài tiếng nói",
    "TNVN",
    "R&D",
]


def reformulate_query(query: str) -> tuple[str, str]:
    """Reformulate a user query for both dense and sparse retrieval.

    Returns:
        tuple[str, str]: (cleaned_semantic_query, fts_search_query)
        - cleaned_semantic_query: Corrected typos, suitable for dense embeddings.
        - fts_search_query: High-recall, high-precision websearch string for PostgreSQL FTS.
    """
    normalized = query.strip()

    # 1. Apply typo corrections
    for pattern, replacement in TYPO_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)

    # 2. Strip conversational fillers to produce a cleaner semantic query
    cleaned = normalized
    for filler in CONVERSATIONAL_FILLERS:
        cleaned = filler.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 3. Extract matched key phrases for PostgreSQL FTS OR-combination
    matched_phrases: list[str] = []
    lower_norm = normalized.lower()
    for kp in KEY_PHRASES:
        if kp.lower() in lower_norm:
            phrase = f'"{kp}"'
            if phrase not in matched_phrases:
                matched_phrases.append(phrase)

    # 4. Extract years (e.g. 2026) and decision numbers
    for num in re.findall(r"\b\d{4}\b", normalized):
        item = f'"{num}"'
        if item not in matched_phrases:
            matched_phrases.append(item)

    # 5. Extract decision numbers like "số 42", "42/QĐ", "90/QĐ"
    for qd_match in re.findall(r"\b(\d{1,4})\s*/\s*qđ", normalized, re.IGNORECASE):
        item = f'"{qd_match}"'
        if item not in matched_phrases:
            matched_phrases.append(item)

    if matched_phrases:
        fts_query = " OR ".join(matched_phrases)
    else:
        fts_query = cleaned or normalized

    return cleaned or normalized, fts_query

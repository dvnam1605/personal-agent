"""Extensible Query Reformulation and Normalization for Multi-Domain RAG Retrieval.

Provides deterministic query cleaning, universal Vietnamese spelling variant generation,
conversational noise removal, dynamic entity-aware full-text search formulation,
and intelligent LLM query rewriting fallback.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.core.config import settings
from app.services.retrieval.conversational_filter import (
    UNIVERSAL_TYPOS,
    clean_conversational_phrasing,
    extract_potential_honorific_names,
)
from app.services.retrieval.entity_catalog import (
    DynamicEntityCatalog,
    get_entity_catalog,
)
from app.services.retrieval.vietnamese_orthography import (
    generate_orthographic_variants,
    normalize_unicode,
)

logger = logging.getLogger(__name__)

# Export for backward compatibility
TYPO_REPLACEMENTS = UNIVERSAL_TYPOS

# Regex patterns for administrative and document identifiers (generic)
_DOC_NUMBER_PATTERN = re.compile(
    r"\b(?:số\s+)?(\d{1,5}(?:\s*/\s*[A-ZĐa-zđ\-_]+)?)\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(19\d\d|20\d\d)\b")


def reformulate_query(
    query: str,
    catalog: DynamicEntityCatalog | None = None,
) -> tuple[str, str]:
    """Reformulate a user query for both dense vector and sparse lexical retrieval deterministically.

    This function operates dynamically without hardcoding specific dataset entities:
    1. Normalizes Unicode to standard NFC.
    2. Corrects common typographical errors.
    3. Matches entities dynamically from the DB Entity Catalog (signers, doc types, authorities).
    4. Dynamically extracts any person names with honorifics/titles and generates
       their full Vietnamese spelling variants (e.g. y/i interchangeability) on the fly.
    5. Extracts specific document numbers and years.
    6. Strips conversational question noise to produce a clean semantic query for embeddings.

    Args:
        query: The raw query from the user.
        catalog: Optional custom DynamicEntityCatalog. If None, uses the global singleton.

    Returns:
        tuple[str, str]: (cleaned_semantic_query, fts_search_query)
    """
    normalized = normalize_unicode(query)

    # 1. Apply typos
    for pattern, replacement in UNIVERSAL_TYPOS:
        normalized = pattern.sub(replacement, normalized)

    matched_phrases: list[str] = []

    # 2. Dynamic Entity Matching from Catalog (if loaded)
    cat = catalog or get_entity_catalog()
    if cat:
        entity_matches = cat.match_entities(normalized)
        for _, variants in entity_matches:
            for v in variants:
                phrase = f'"{v}"'
                if phrase not in matched_phrases:
                    matched_phrases.append(phrase)

    # 3. Dynamic Honorific / Person Name Extraction
    detected_names = extract_potential_honorific_names(normalized)
    for raw_name in detected_names:
        variants = generate_orthographic_variants(raw_name)
        for v in variants:
            phrase = f'"{v}"'
            if phrase not in matched_phrases:
                matched_phrases.append(phrase)

    # 4. Extract specific document numbers (e.g. "số 427", "1367/QĐ", "4351")
    for m in _DOC_NUMBER_PATTERN.finditer(normalized):
        token = m.group(1).strip()
        if "/" in token or (token.isdigit() and len(token) <= 4 and int(token) > 0):
            clean_num = re.sub(r"\s*/\s*", "/", token)
            phrase = f'"{clean_num}"'
            if phrase not in matched_phrases:
                matched_phrases.append(phrase)

    # 5. Extract 4-digit years (e.g. 2024, 2025, 2026)
    for m in _YEAR_PATTERN.finditer(normalized):
        year = m.group(1)
        phrase = f'"{year}"'
        if phrase not in matched_phrases:
            matched_phrases.append(phrase)

    # 6. Strip conversational noise for clean semantic embedding query
    cleaned_semantic = clean_conversational_phrasing(normalized)

    # 7. Construct PostgreSQL FTS websearch query
    if matched_phrases:
        fts_search_query = " OR ".join(matched_phrases)
    else:
        fts_search_query = cleaned_semantic or normalized

    return cleaned_semantic or normalized, fts_search_query


async def areformulate_with_llm(query: str) -> tuple[str, str]:
    """Rewrite and expand query using LLM for difficult, ambiguous, or multi-faceted queries."""
    api_key = str(settings.llm.openai_api_key or "")
    url = settings.llm.chat_completions_url()
    timeout = settings.timeouts.llm_request_seconds

    prompt = (
        "Bạn là chuyên gia xử lý truy vấn cho hệ thống tìm kiếm văn bản hành chính RAG.\n"
        "Hãy phân tích câu hỏi sau của người dùng và trả về JSON:\n"
        "- semantic_query: câu hỏi chuẩn hóa ngắn gọn để tìm kiếm vector embeddings (loại bỏ từ đàm thoại, giữ ý định chính)\n"
        "- keywords: danh sách các từ/cụm từ khóa quan trọng để tìm kiếm từ khóa FTS (gồm tên người, chức vụ, loại văn bản, từ khóa chính)\n\n"
        f'Câu hỏi: "{query}"\n\n'
        "Chỉ trả lời đúng định dạng JSON trong cặp ```json ``` hoặc thuần JSON:\n"
        '{"semantic_query": "...", "keywords": ["..."]}\n'
    )

    chunks: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.llm.primary_model,
                "messages": [
                    {"role": "system", "content": "Bạn là chuyên gia trích xuất truy vấn RAG. Chỉ trả về JSON duy nhất."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
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
                except Exception:
                    continue

    raw = "".join(chunks).strip()
    clean_json = re.sub(r"^```(?:json)?\s*", "", raw)
    clean_json = re.sub(r"\s*```$", "", clean_json.strip())
    data = json.loads(clean_json)

    semantic = data.get("semantic_query") or query
    keywords = data.get("keywords") or []
    phrases = [f'"{kw}"' for kw in keywords if kw]
    fts_query = " OR ".join(phrases) if phrases else semantic

    return semantic, fts_query


async def areformulate_query(
    query: str,
    catalog: DynamicEntityCatalog | None = None,
    use_llm_fallback: bool = True,
) -> tuple[str, str]:
    """Asynchronously reformulate query with deterministic rules first, falling back to LLM rewriting."""
    cleaned_semantic, fts_query = reformulate_query(query, catalog=catalog)

    # If deterministic reformulation matched concrete entities/phrases, return immediately (0ms, 0 tokens)
    has_exact_phrases = '"' in fts_query

    if has_exact_phrases or not use_llm_fallback:
        return cleaned_semantic, fts_query

    # Fallback to LLM query reformulation when no specific entities or keywords were extracted
    try:
        llm_semantic, llm_fts = await areformulate_with_llm(query)
        return llm_semantic or cleaned_semantic, llm_fts or fts_query
    except Exception as exc:
        logger.warning("llm_query_reformulation_fallback_failed error=%s", exc)
        return cleaned_semantic, fts_query

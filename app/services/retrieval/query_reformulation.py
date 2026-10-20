"""Extensible Query Reformulation and Normalization for Multi-Domain RAG Retrieval.

Provides deterministic query cleaning, universal Vietnamese spelling variant generation,
conversational noise removal, dynamic entity-aware full-text search formulation,
and intelligent LLM query rewriting fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.core.jsonutil import extract_json_object
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
    # Phrases precise enough to stand alone (people, organisations, numbers,
    # years). A bare document-type match ("báo cáo") is not: the rest of the
    # question's keywords must stay in the query or recall collapses.
    precise = False

    # 2. Dynamic Entity Matching from Catalog (if loaded)
    cat = catalog or get_entity_catalog()
    if cat:
        entity_matches = cat.match_entities(normalized)
        for name, variants in entity_matches:
            entity = getattr(cat, "_entities", {}).get(name)
            if entity is None or entity.category != "document_type":
                precise = True
            for v in variants:
                phrase = f'"{v}"'
                if phrase not in matched_phrases:
                    matched_phrases.append(phrase)

    # 3. Dynamic Honorific / Person Name Extraction
    detected_names = extract_potential_honorific_names(normalized)
    for raw_name in detected_names:
        precise = True
        variants = generate_orthographic_variants(raw_name)
        for v in variants:
            phrase = f'"{v}"'
            if phrase not in matched_phrases:
                matched_phrases.append(phrase)

    # 4. Extract specific document numbers (e.g. "số 427", "1367/QĐ", "4351")
    # A bare number only counts when it is introduced as one ("số 427"):
    # "Điều 5", "khoản 2" or "3 người" are not document numbers.
    for m in _DOC_NUMBER_PATTERN.finditer(normalized):
        token = m.group(1).strip()
        is_number = "/" in token or (
            token.isdigit()
            and len(token) <= 4
            and int(token) > 0
            and m.group(0).lower().startswith("số")
        )
        if is_number:
            precise = True
            clean_num = re.sub(r"\s*/\s*", "/", token)
            phrase = f'"{clean_num}"'
            if phrase not in matched_phrases:
                matched_phrases.append(phrase)

    # 5. Extract 4-digit years (e.g. 2024, 2025, 2026)
    for m in _YEAR_PATTERN.finditer(normalized):
        precise = True
        year = m.group(1)
        phrase = f'"{year}"'
        if phrase not in matched_phrases:
            matched_phrases.append(phrase)

    # 6. Strip conversational noise for clean semantic embedding query
    cleaned_semantic = clean_conversational_phrasing(normalized)

    # 7. Construct PostgreSQL FTS websearch query
    if matched_phrases and not precise:
        extra = fallback_fts_query(cleaned_semantic or normalized).split(" OR ")
        terms = matched_phrases + [
            t for t in extra if t.startswith('"') and t not in matched_phrases
        ]
        fts_search_query = " OR ".join(terms)
    elif matched_phrases:
        fts_search_query = " OR ".join(matched_phrases)
    else:
        fts_search_query = cleaned_semantic or normalized

    return cleaned_semantic or normalized, fts_search_query


_extract_json_object = extract_json_object


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
    # httpx timeouts are per read; bound the whole streamed call as well.
    async with (
        asyncio.timeout(timeout),
        httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client,
    ):
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.llm.primary_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Bạn là chuyên gia trích xuất truy vấn RAG. Chỉ trả về JSON duy nhất.",
                    },
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

    data = _extract_json_object("".join(chunks))

    semantic = str(data.get("semantic_query") or query)
    keywords = data.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(kw).replace('"', " ").strip() for kw in keywords]
    phrases = [f'"{kw}"' for kw in keywords if kw]
    fts_query = " OR ".join(phrases) if phrases else semantic

    return semantic, fts_query


# Function syllables that match almost every chunk; OR-ing them only adds noise.
_FTS_STOPWORDS = frozenset(
    {
        "của",
        "là",
        "và",
        "các",
        "những",
        "cho",
        "với",
        "trong",
        "có",
        "được",
        "này",
        "đó",
        "thì",
        "mà",
        "về",
        "theo",
        "từ",
        "tại",
        "một",
        "nào",
        "gì",
        "không",
        "như",
        "khi",
        "đã",
        "sẽ",
        "đang",
        "hay",
        "hoặc",
        "bị",
        "ra",
        "vào",
        "để",
        "ở",
        "nhé",
        "vậy",
        "sao",
        "the",
        "of",
        "and",
        "or",
    }
)


def fallback_fts_query(text: str) -> str:
    """OR adjacent-syllable phrases of a free-text question for FTS.

    ``websearch_to_tsquery`` ANDs every bare word, so a full Vietnamese
    question (one token per syllable) almost never matches a single chunk.
    Single syllables OR-ed together match most of the corpus instead
    ("báo" / "cáo"), so the unit is the syllable bigram: Vietnamese compound
    words are mostly two syllables, and a quoted pair is a phrase match.
    Code-like tokens (``80/QĐ``, ``2026``) are kept as standalone terms; lone
    short digits are dropped as noise. Measured on the live corpus: 0 hits
    (AND) vs. relevant top-5 with bigram OR.
    """
    tokens = [raw.strip("/_'") for raw in re.findall(r"[\w'/]+", text)]
    tokens = [tok for tok in tokens if len(tok) >= 2 or tok.isdigit()]

    terms: list[str] = []

    def _add(term: str) -> None:
        quoted = f'"{term}"'
        if quoted not in terms:
            terms.append(quoted)

    for left, right in zip(tokens, tokens[1:], strict=False):
        if left.lower() in _FTS_STOPWORDS or right.lower() in _FTS_STOPWORDS:
            continue
        _add(f"{left} {right}")
    for tok in tokens:
        if any(ch.isdigit() for ch in tok) and ("/" in tok or len(tok) >= 3):
            _add(tok)
    if not terms:  # one-word question: plain significant terms
        for tok in tokens:
            if tok.lower() not in _FTS_STOPWORDS and (len(tok) >= 2 or tok.isdigit()):
                _add(tok)
    return " OR ".join(terms) if terms else text


# LLM rewrites are shared by the dense and sparse branches (which run
# concurrently) and by retry rounds, so one question costs at most one call.
_LLM_CACHE_TTL_SECONDS = 600.0
_LLM_FAILURE_TTL_SECONDS = 30.0
_LLM_CACHE_MAX = 256
_llm_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}
_llm_inflight: dict[tuple[int, str], asyncio.Future[tuple[str, str] | None]] = {}


def clear_reformulation_cache() -> None:
    """Drop cached LLM rewrites (tests, config reloads)."""
    _llm_cache.clear()
    _llm_inflight.clear()


async def _cached_llm_reformulation(query: str) -> tuple[str, str] | None:
    """Single-flight + TTL cache around :func:`areformulate_with_llm`.

    Returns ``None`` when the LLM failed; failures are cached briefly so a
    flaky gateway (429 / timeout) is not hammered by the parallel branches
    and the retry round.
    """
    now = time.monotonic()
    cached = _llm_cache.get(query)
    if cached is not None and cached[0] > now:
        return cached[1]

    loop = asyncio.get_running_loop()
    key = (id(loop), query)
    pending = _llm_inflight.get(key)
    if pending is not None:
        return await asyncio.shield(pending)

    future: asyncio.Future[tuple[str, str] | None] = loop.create_future()
    _llm_inflight[key] = future
    result: tuple[str, str] | None = None
    settled = False
    try:
        result = await areformulate_with_llm(query)
        settled = True
    except Exception as exc:
        logger.warning("llm_query_reformulation_fallback_failed error=%s", exc)
        settled = True
    finally:
        # A cancelled caller is not an LLM verdict: release waiters, cache nothing.
        if settled:
            ttl = _LLM_CACHE_TTL_SECONDS if result is not None else _LLM_FAILURE_TTL_SECONDS
            if len(_llm_cache) >= _LLM_CACHE_MAX:
                _llm_cache.pop(next(iter(_llm_cache)))
            _llm_cache[query] = (time.monotonic() + ttl, result)
        _llm_inflight.pop(key, None)
        if not future.done():
            future.set_result(result)
    return result


async def areformulate_query(
    query: str,
    catalog: DynamicEntityCatalog | None = None,
    use_llm_fallback: bool = True,
) -> tuple[str, str]:
    """Asynchronously reformulate query with deterministic rules first, falling back to LLM rewriting."""
    cleaned_semantic, fts_query = reformulate_query(query, catalog=catalog)

    # If deterministic reformulation matched concrete entities/phrases, return immediately (0ms, 0 tokens)
    has_exact_phrases = '"' in fts_query

    if has_exact_phrases:
        return cleaned_semantic, fts_query

    fts_fallback = fallback_fts_query(fts_query)
    if not use_llm_fallback:
        return cleaned_semantic, fts_fallback

    # Fallback to LLM query reformulation when no specific entities or keywords were extracted
    rewritten = await _cached_llm_reformulation(query)
    if rewritten is None:
        return cleaned_semantic, fts_fallback
    llm_semantic, llm_fts = rewritten
    if llm_fts and '"' not in llm_fts:
        llm_fts = fallback_fts_query(llm_fts)
    return llm_semantic or cleaned_semantic, llm_fts or fts_fallback

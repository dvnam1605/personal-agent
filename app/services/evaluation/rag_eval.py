"""Gold-set evaluation of the knowledge pipeline through the real HTTP API.

Each item runs exactly like a user question (POST /query) and is scored
from what the system returned *and* what it actually showed the model
(GET /runs/{id}/evidence). Metrics are deterministic; LLM-dependent ones
are reported as ``skipped`` when the gateway is unavailable instead of
counting as failures, so runs stay comparable across outages.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.text import unaccent_vietnamese

LLM_DOWN_MARKER = "Không thể kết nối đến mô hình AI"
_COUNT_RE = re.compile(r"(?:Có|Tìm thấy)\s+\*\*(\d+)\*\*")
_NUMBER_ATTR_RE = re.compile(r'number="([^"]+)"')


def norm_number(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", unaccent_vietnamese(value))


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", unaccent_vietnamese(value)).strip()


def load_gold(path: Path, split: str | None = None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["items"]
    return [i for i in items if split in (None, "all") or i.get("split") == split]


@dataclass
class ItemResult:
    id: str
    type: str
    metrics: dict[str, float | None] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "metrics": self.metrics,
            "details": self.details,
            "latency_s": round(self.latency_s, 2),
            "error": self.error,
        }


def _evidence_numbers(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        m = _NUMBER_ATTR_RE.search(row.get("prompt_text") or "")
        if m:
            out.add(norm_number(m.group(1)))
    return out


def _matches_expected(row: dict[str, Any], item: dict[str, Any]) -> bool:
    numbers = {norm_number(n) for n in item.get("expected_doc_numbers", [])}
    if numbers and _evidence_numbers([row]) & numbers:
        return True
    titles = [norm_text(t) for t in item.get("expected_titles", [])]
    haystack = norm_text(f"{row.get('title') or ''} {row.get('prompt_text') or ''}")
    return any(t and t in haystack for t in titles)


def score_metadata(item: dict[str, Any], body: dict[str, Any]) -> dict[str, float | None]:
    message = body.get("message") or ""
    metrics: dict[str, float | None] = {}
    if "expected_count" in item:
        m = _COUNT_RE.search(message)
        metrics["count_exact"] = float(bool(m) and int(m.group(1)) == item["expected_count"])
    if "expected_doc_numbers" in item:
        wanted = [norm_number(n) for n in item["expected_doc_numbers"]]
        metrics["latest_doc"] = float(all(w in norm_number(message) for w in wanted))
    citations = (body.get("data") or {}).get("citations") or []
    metrics["route_metadata"] = float(
        bool(citations)
        and all(c.get("citation_type") == "document" for c in citations)
        or ("expected_count" in item and item["expected_count"] == 0)
    )
    return metrics


def score_content(
    item: dict[str, Any], body: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, float | None]:
    message = body.get("message") or ""
    llm_down = LLM_DOWN_MARKER in message
    metrics: dict[str, float | None] = {}
    metrics["retrieval_doc_hit"] = float(any(_matches_expected(r, item) for r in evidence))
    quotes = [norm_text(q) for q in item.get("gold_quotes", [])]
    metrics["retrieval_quote_hit"] = (
        float(any(q in norm_text(r.get("prompt_text") or "") for r in evidence for q in quotes))
        if quotes
        else None
    )
    cited = [r for r in evidence if r.get("cited")]
    if llm_down:
        metrics["citation_precision"] = None
        metrics["answer_must_include"] = None
        metrics["answer_cited"] = None
    else:
        metrics["answer_cited"] = float(bool(cited))
        metrics["citation_precision"] = (
            sum(_matches_expected(r, item) for r in cited) / len(cited) if cited else 0.0
        )
        must = [norm_text(m) for m in item.get("must_include", [])]
        metrics["answer_must_include"] = (
            float(all(m in norm_text(message) for m in must)) if must else None
        )
    return metrics


def score_negative(item: dict[str, Any], body: dict[str, Any]) -> dict[str, float | None]:
    sufficiency = ((body.get("data") or {}).get("sufficiency") or "").upper()
    if sufficiency == "INSUFFICIENT":
        return {"abstain_correct": 1.0}
    # Without the LLM the pipeline cannot judge relevance and lists related
    # documents instead: not a verdict either way, so do not score it.
    if LLM_DOWN_MARKER in (body.get("message") or ""):
        return {"abstain_correct": None}
    return {"abstain_correct": 0.0}


class RagEvaluator:
    def __init__(self, base_url: str, *, timeout: float = 300.0, headers: dict | None = None):
        self._client = httpx.Client(base_url=base_url, timeout=timeout, headers=headers or {})

    def close(self) -> None:
        self._client.close()

    def run_item(self, item: dict[str, Any]) -> ItemResult:
        result = ItemResult(id=item["id"], type=item["type"])
        started = time.perf_counter()
        try:
            if item["type"] == "generation":
                body = self._client.post("/documents/outline", json={"request": item["query"]})
                body.raise_for_status()
                data = body.json()
                titles = data.get("outline", {}).get("source_titles", [])
                keywords = [norm_text(k) for k in item.get("expected_source_title_keywords", [])]
                relevant = sum(1 for t in titles if any(k in norm_text(t) for k in keywords))
                result.metrics = {
                    "template_correct": float(
                        data.get("template_key") == item["expected_template"]
                    ),
                    "relevant_sources": float(relevant >= item.get("min_relevant_sources", 1)),
                }
                result.details = {"source_titles": titles, "relevant": relevant}
            else:
                resp = self._client.post("/query", json={"query": item["query"]})
                resp.raise_for_status()
                body = resp.json()
                evidence: list[dict[str, Any]] = []
                if body.get("run_id"):
                    ev = self._client.get(f"/runs/{body['run_id']}/evidence")
                    if ev.status_code == 200:
                        evidence = ev.json().get("evidence", [])
                if item["type"] == "metadata":
                    result.metrics = score_metadata(item, body)
                elif item["type"] == "negative":
                    result.metrics = score_negative(item, body)
                else:
                    result.metrics = score_content(item, body, evidence)
                result.details = {
                    "run_id": body.get("run_id"),
                    "sufficiency": (body.get("data") or {}).get("sufficiency"),
                    "evidence_rows": len(evidence),
                    "llm_down": LLM_DOWN_MARKER in (body.get("message") or ""),
                    "answer_head": (body.get("message") or "")[:240],
                }
        except Exception as exc:  # noqa: BLE001 - one failing item must not stop the run
            result.error = f"{type(exc).__name__}: {exc}"[:300]
        result.latency_s = time.perf_counter() - started
        return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean of every metric over the items that measured it (None = skipped)."""
    sums: dict[str, list[float]] = {}
    skipped: dict[str, int] = {}
    for r in results:
        for name, value in (r.get("metrics") or {}).items():
            if value is None:
                skipped[name] = skipped.get(name, 0) + 1
            else:
                sums.setdefault(name, []).append(float(value))
    return {
        "items": len(results),
        "errors": sum(1 for r in results if r.get("error")),
        "metrics": {k: round(sum(v) / len(v), 4) for k, v in sorted(sums.items())},
        "measured": {k: len(v) for k, v in sorted(sums.items())},
        "skipped": dict(sorted(skipped.items())),
        "mean_latency_s": round(
            sum(r.get("latency_s", 0.0) for r in results) / max(len(results), 1), 2
        ),
    }


def compare(current: dict[str, Any], baseline: dict[str, Any], tolerance: float) -> list[str]:
    """Metrics that regressed by more than ``tolerance`` versus the baseline."""
    regressions: list[str] = []
    for name, base in baseline.get("metrics", {}).items():
        now = current.get("metrics", {}).get(name)
        if now is not None and now < base - tolerance:
            regressions.append(f"{name}: {base:.3f} -> {now:.3f}")
    return regressions

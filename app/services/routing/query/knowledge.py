"""Knowledge answers: structured SQL routes first, then (scoped) corpus RAG.

A knowledge question is answered from relation facts, document metadata, a
profile-scoped RAG pass, or plain corpus RAG, in that order of preference; any
failure in a structured route degrades to corpus RAG.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.retrieval.answer.numbering import renumber

from .followup import _FOLLOWUP_SCOPE, _RAG_MAX_SCOPED_DOCUMENTS
from .models import QueryResult, QueryRouteInfo
from .parsing import retrieval_requester_id

# Records keep the orchestrator's logger name.
logger = logging.getLogger("app.services.routing.query.orchestrator")

# With the reranker disabled RRF order is final, so keep only the strongest chunks.
_RAG_TOP_K_DENSE = 50
_RAG_TOP_K_SPARSE = 60
_RAG_MAX_CANDIDATES = 16
_RAG_PER_DOCUMENT_CAP = 4
_RAG_CONTEXT_TOKEN_BUDGET = 8192


def _mentions(text: str, target: str) -> bool:
    """Whether ``text`` names ``target`` (accent/punctuation-insensitive prefix)."""
    from app.core.text import unaccent_vietnamese

    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", "", unaccent_vietnamese(s).lower())

    key = norm(target)[:24]
    return not key or key in norm(text)


@dataclass
class _KnowledgeRoute:
    """Either a finished structured answer or the RAG query to run."""

    answer: str | None = None
    citations: list[Any] = field(default_factory=list)
    status: Any = None
    rag_query: Any = None
    prefix: str = ""
    # Relation facts from SQL whose documents are read before answering.
    relation: Any = None


class KnowledgeMixin:
    """Knowledge / RAG / relation answering for ``QueryOrchestrator``."""

    async def _handle_knowledge(
        self,
        user_id: str,
        query: str,
        run_id: str,
        route: QueryRouteInfo,
        conversation_history: list[dict[str, str]] | None = None,
        session: AsyncSession | None = None,
    ) -> QueryResult:
        synthesis = await self._run_retrieval(
            query, user_id, conversation_history=conversation_history
        )
        citations = [
            citation.model_dump(mode="json")
            for citation in list(getattr(synthesis, "citations", []))
        ]
        status = getattr(getattr(synthesis, "status", None), "value", None)
        await self._record_evidence(
            session,
            run_id,
            evidence=getattr(synthesis, "evidence", None),
            retrieval_trace_id=getattr(synthesis, "retrieval_trace_id", None),
            answer_status=status,
        )
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=renumber(str(getattr(synthesis, "answer", ""))),
            route=route,
            data={"citations": citations, "sufficiency": status},
        )

    @staticmethod
    async def _record_evidence(
        session: AsyncSession | None,
        run_id: str,
        *,
        evidence: Any,
        retrieval_trace_id: str | None,
        answer_status: str | None,
    ) -> None:
        """Persist what the model saw for this run (best-effort, never fails the answer)."""
        if session is None:
            return
        try:
            from app.infrastructure.db.evidence_repository import record_run_evidence

            await record_run_evidence(
                session,
                run_id,
                evidence=list(evidence or []),
                retrieval_trace_id=retrieval_trace_id,
                answer_status=answer_status,
            )
        except Exception:  # noqa: BLE001 - audit trail must not break answering
            logger.warning("run_evidence_record_failed", extra={"run_id": run_id}, exc_info=True)

    def _live_pipeline(self) -> Any:
        if self._pipeline is None:
            from app.services.retrieval.factory import build_retrieval_pipeline

            self._pipeline = build_retrieval_pipeline(
                use_viranker=False,
                per_document_cap=_RAG_PER_DOCUMENT_CAP,
                rerank_top_k_max=_RAG_MAX_CANDIDATES,
            )
        return self._pipeline

    @staticmethod
    def _live_retrieval_query(query: str, user_id: str) -> Any:
        from app.domain.models.retrieval import RetrievalQuery

        return RetrievalQuery(
            original_query=query,
            search_query=query,
            requester_id=retrieval_requester_id(user_id),
            top_k_dense=_RAG_TOP_K_DENSE,
            top_k_sparse=_RAG_TOP_K_SPARSE,
            context_token_budget=_RAG_CONTEXT_TOKEN_BUDGET,
        )

    def _metadata(self) -> Any:
        if self._metadata_service is None:
            from app.services.retrieval.metadata import MetadataLookupService

            self._metadata_service = MetadataLookupService()
        return self._metadata_service

    def _relations(self) -> Any:
        if self._relation_service is None:
            from app.services.retrieval.relations import RelationLookupService

            self._relation_service = RelationLookupService()
        return self._relation_service

    async def _route_knowledge(self, query: str, user_id: str) -> _KnowledgeRoute:
        """Pick SQL-over-relations, SQL-over-profiles, profile-scoped RAG, or corpus RAG.

        Any failure in the structured routes degrades to plain corpus RAG.
        """
        from app.domain.models.retrieval import RetrievalMode
        from app.domain.models.retrieval.metadata_filter import KnowledgeIntent
        from app.domain.models.retrieval.sufficiency import SufficiencyStatus
        from app.services.retrieval.metadata import render_metadata_answer
        from app.services.retrieval.metadata.render import describe_filters, describe_noun

        base_query = self._live_retrieval_query(query, user_id)
        requester = retrieval_requester_id(user_id)
        scope = _FOLLOWUP_SCOPE.get()
        if scope is not None:
            # Search the cited documents with the previous answer's own words.
            document_ids, anchor = scope
            logger.info("knowledge_route_planned", extra={"intent": "followup_scope"})
            return _KnowledgeRoute(
                rag_query=base_query.model_copy(
                    update={
                        "mode": RetrievalMode.DOCUMENT_SEARCH,
                        "document_ids": document_ids,
                        "search_query": f"{anchor} {query}"[:600],
                        "require_source_diversity": len(document_ids) > 1,
                    }
                )
            )
        try:
            relation = await self._relations().answer(query, requester)
            if relation is not None:
                logger.info(
                    "knowledge_route_planned", extra={"intent": "relation", **relation.trace}
                )
                if 0 < relation.total <= _RAG_MAX_SCOPED_DOCUMENTS:
                    # Facts give the documents and who; their text supplies the details.
                    return _KnowledgeRoute(rag_query=base_query, relation=relation)
                return _KnowledgeRoute(
                    answer=await self._phrase(query, relation.answer),
                    citations=relation.citations,
                    status=SufficiencyStatus.SUFFICIENT,
                )
        except Exception:  # noqa: BLE001 - the relation route must never break answering
            logger.exception("knowledge_relation_route_failed")
        try:
            service = self._metadata()
            plan = await service.plan(query, requester)
            logger.info(
                "knowledge_route_planned",
                extra={"intent": plan.intent.value, "reason": plan.reason},
            )
            if plan.intent is KnowledgeIntent.METADATA:
                result = await service.run(plan.spec, requester)
                answer, citations = render_metadata_answer(plan.spec, result)
                answer = await self._phrase(query, answer)
                status = (
                    SufficiencyStatus.SUFFICIENT if result.total else SufficiencyStatus.INSUFFICIENT
                )
                return _KnowledgeRoute(answer=answer, citations=citations, status=status)
            if plan.intent is KnowledgeIntent.METADATA_THEN_CONTENT:
                ids, total = await service.resolve_document_ids(
                    plan.spec, requester, cap=_RAG_MAX_SCOPED_DOCUMENTS
                )
                if total == 0:
                    note = (
                        f"(Không có {describe_noun(plan.spec)} nào"
                        f"{describe_filters(plan.spec)}; tìm trên toàn bộ kho tài liệu.)\n\n"
                    )
                    return _KnowledgeRoute(rag_query=base_query, prefix=note)
                if total > _RAG_MAX_SCOPED_DOCUMENTS:
                    result = await service.run(plan.spec, requester)
                    answer, citations = render_metadata_answer(plan.spec, result)
                    answer += (
                        f"\n\nCó {total} tài liệu khớp, quá nhiều để đọc nội dung cùng lúc; "
                        "hãy nêu rõ hơn (số hiệu, thời gian, người liên quan)."
                    )
                    return _KnowledgeRoute(
                        answer=answer, citations=citations, status=SufficiencyStatus.PARTIAL
                    )
                scoped = base_query.model_copy(
                    update={
                        "mode": RetrievalMode.DOCUMENT_SEARCH,
                        "document_ids": ids,
                        "require_source_diversity": len(ids) > 1,
                    }
                )
                return _KnowledgeRoute(rag_query=scoped)
        except Exception:  # noqa: BLE001 - the metadata route must never break answering
            logger.exception("knowledge_metadata_route_failed")
        return _KnowledgeRoute(rag_query=base_query)

    async def _phrase(self, question: str, draft: str) -> str:
        """Natural wording for a SQL-backed answer (facts unchanged; draft on doubt)."""
        if not self._phrase_structured:
            return draft
        from app.services.retrieval.answer.verbalize import phrase_structured_answer

        try:
            return await phrase_structured_answer(question, draft)
        except Exception:  # noqa: BLE001 - wording must never break answering
            logger.exception("structured_phrasing_failed")
            return draft

    async def _answer_relation(
        self,
        query: str,
        route: _KnowledgeRoute,
        conversation_history: list[dict[str, str]] | None,
    ) -> Any:
        """Relation facts from SQL + the text of those documents → one LLM answer.

        Facts go in the system prompt so grounding checks still judge the user's own
        question; omitted targets are appended, and failures fall back to the fact list.
        """
        from app.domain.models.retrieval import RetrievalMode
        from app.domain.models.retrieval.sufficiency import SufficiencyStatus
        from app.services.retrieval.answer.prompts import SYNTHESIS_SYSTEM_PROMPT
        from app.services.retrieval.answer.synthesis import SynthesisResult

        relation = route.relation
        # With signers plus another relation, the signers are what was asked.
        types = {str(f["relation_type"]) for f in relation.facts}
        asked = {"signed_by"} if "signed_by" in types and len(types) > 1 else types
        targets = list(
            dict.fromkeys(
                str(f["target_text"]) for f in relation.facts if f["relation_type"] in asked
            )
        )
        facts_text = re.sub(r"\s*\[\d+\]", "", relation.answer)
        scoped = route.rag_query.model_copy(
            update={
                "mode": RetrievalMode.DOCUMENT_SEARCH,
                "document_ids": [c.document_id for c in relation.citations],
                "require_source_diversity": len(relation.citations) > 1,
                "search_query": f"{query} {' '.join(targets)}"[:500],
            }
        )
        system_prompt = (
            f"{SYNTHESIS_SYSTEM_PROMPT}\n"
            "DỮ KIỆN ĐÃ TRA CHÍNH XÁC TỪ CƠ SỞ DỮ LIỆU (đầy đủ cho câu hỏi này):\n"
            f"{facts_text}\n\n"
            "Yêu cầu thêm:\n"
            "- Dữ kiện trên đã được xác minh từ văn bản gốc: NÊU TRỰC TIẾP kể cả khi đoạn "
            "bằng chứng E# không chứa thông tin đó (khi ấy không cần nhãn [E#] cho câu đó). "
            "Tuyệt đối không nói dữ kiện nào đó 'không có' hay 'chưa thể hiện'.\n"
            "- Nêu ĐỦ mọi văn bản và mọi đối tượng trong dữ kiện trên, không bỏ sót.\n"
            "- Trả lời đúng trọng tâm câu hỏi: chỉ thêm chi tiết từ bằng chứng E# (chức vụ, đơn "
            "vị, ngày hiệu lực, lý do…) khi nó trực tiếp trả lời câu hỏi, gắn trích dẫn [E#].\n"
            "- Văn phong tự nhiên như người trả lời đồng nghiệp: mở đầu bằng câu trả lời thẳng; "
            "1–3 văn bản viết thành đoạn văn ngắn, nhiều hơn thì danh sách gạch đầu dòng gọn, "
            "mỗi văn bản một dòng. Không dùng tiêu đề (#), không kẻ ngang (---), hạn chế in đậm; "
            "không mở đầu kiểu 'Dựa trên các tài liệu được cung cấp', 'Dưới đây là'.\n"
            "- Không nêu điều bằng chứng không có, không viết câu kiểu 'chưa có thông tin…'; "
            "không nhắc tới 'dữ kiện' hay 'cơ sở dữ liệu'."
        )
        try:
            result = await self._live_pipeline().run_with_synthesis(
                scoped,
                internal_only=True,
                conversation_history=conversation_history,
                system_prompt=system_prompt,
            )
        except Exception:  # noqa: BLE001 - fall back to the fact list
            logger.exception("relation_content_answer_failed")
            result = None
        if result is None or not result.citations:
            return SynthesisResult(
                answer=await self._phrase(query, relation.answer),
                citations=relation.citations,
                status=SufficiencyStatus.SUFFICIENT,
            )
        missing = [t for t in targets if not _mentions(result.answer, t)]
        if missing:
            logger.info("relation_answer_missing_targets count=%d", len(missing))
            result = result.model_copy(
                update={
                    "answer": result.answer.rstrip()
                    + "\n\nNgoài ra, theo các văn bản trên còn có: "
                    + "; ".join(missing)
                    + "."
                }
            )
        if result.status is not SufficiencyStatus.SUFFICIENT:
            # The facts themselves are exact; missing detail is not missing answer.
            result = result.model_copy(update={"status": SufficiencyStatus.SUFFICIENT})
        return result

    async def _run_retrieval(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Any:
        if self._retrieve is not None:
            return await self._retrieve(query, user_id)
        from app.services.retrieval.answer.synthesis import SynthesisResult

        route = await self._route_knowledge(query, user_id)
        if route.relation is not None:
            return await self._answer_relation(query, route, conversation_history)
        if route.answer is not None:
            return SynthesisResult(
                answer=route.answer, citations=route.citations, status=route.status
            )
        result = await self._live_pipeline().run_with_synthesis(
            route.rag_query,
            internal_only=True,
            conversation_history=conversation_history,
        )
        if route.prefix:
            result = result.model_copy(update={"answer": route.prefix + result.answer})
        return result

    async def _run_retrieval_stream(
        self,
        query: str,
        user_id: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        route = await self._route_knowledge(query, user_id)
        if route.relation is not None:
            # Not streamed: the answer is checked for missing facts before it is shown.
            result = await self._answer_relation(query, route, conversation_history)
            yield {"type": "token", "delta": result.answer}
            yield {
                "type": "citations",
                "citations": [c.model_dump(mode="json") for c in result.citations],
                "status": result.status.value,
            }
            return
        if route.answer is not None:
            yield {"type": "token", "delta": route.answer}
            yield {
                "type": "citations",
                "citations": [c.model_dump(mode="json") for c in route.citations],
                "status": route.status.value,
            }
            return
        if route.prefix:
            yield {"type": "token", "delta": route.prefix}
        async for event in self._live_pipeline().run_with_streaming_synthesis(
            route.rag_query,
            internal_only=True,
            conversation_history=conversation_history,
        ):
            yield event

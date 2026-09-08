"""WF-02: Document Search & Briefing StateGraph (P15 / §18A.5 / H5 / H6).

Parallel Execution:
1. prepare_search: Initialize search domains from state.pending_domains.
2. dispatch_parallel_branches: Returns Send primitives with user_id and run_id.
3. rag_search & drive_search: Run concurrently; outputs fan into branch_results & branch_errors.
4. synthesize_briefing: Merges collected branch results, detects errors, and propagates
   partial_error when errors exist (H6), preventing silent empty completed reports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.harness.workflow_channels import WorkflowState


def _call_searcher(searcher: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute searcher supporting both dict context and plain query signature."""
    try:
        return searcher(context)
    except TypeError:
        return searcher(context.get("query", ""))


def build_document_briefing_graph(
    *,
    rag_searcher: Callable[..., list[dict[str, Any]]] | None = None,
    drive_searcher: Callable[..., list[dict[str, Any]]] | None = None,
    synthesizer: Callable[[list[dict[str, Any]], str], str] | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the WF-02 Document Search & Briefing StateGraph."""

    def _prepare_search(state: WorkflowState) -> dict[str, Any]:
        pending = state.get("pending_domains") or ["rag", "drive"]
        return {
            "pending_domains": pending,
            "status": "searching_parallel",
        }

    def _dispatch_parallel(state: WorkflowState) -> list[Send]:
        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")
        pending = state.get("pending_domains") or ["rag", "drive"]
        sends: list[Send] = []
        payload = {"query": query, "user_id": user_id, "run_id": run_id}
        if "rag" in pending:
            sends.append(Send("rag_search", payload))
        if "drive" in pending:
            sends.append(Send("drive_search", payload))
        return sends

    def _rag_search(state: WorkflowState) -> dict[str, Any]:
        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")
        context = {"query": query, "user_id": user_id, "run_id": run_id}
        if rag_searcher is not None:
            try:
                items = _call_searcher(rag_searcher, context)
            except Exception as exc:  # noqa: BLE001
                err = f"RAG search error: {exc}"
                return {
                    "errors": [err],
                    "branch_errors": [err],
                    "branch_results": [{"domain": "rag", "items": [], "user_id": user_id}],
                }
        else:
            items = []
        return {
            "branch_results": [{"domain": "rag", "items": items, "user_id": user_id}],
        }

    def _drive_search(state: WorkflowState) -> dict[str, Any]:
        query = state.get("query", "")
        user_id = state.get("user_id", "")
        run_id = state.get("run_id", "")
        context = {"query": query, "user_id": user_id, "run_id": run_id}
        if drive_searcher is not None:
            try:
                items = _call_searcher(drive_searcher, context)
            except Exception as exc:  # noqa: BLE001
                err = f"Drive search error: {exc}"
                return {
                    "errors": [err],
                    "branch_errors": [err],
                    "branch_results": [{"domain": "drive", "items": [], "user_id": user_id}],
                }
        else:
            items = []
        return {
            "branch_results": [{"domain": "drive", "items": items, "user_id": user_id}],
        }

    def _synthesize_briefing(state: WorkflowState) -> dict[str, Any]:
        branch_results = state.get("branch_results", [])
        errors = state.get("errors", [])
        branch_errors = state.get("branch_errors", [])
        all_errors = list(dict.fromkeys([*errors, *branch_errors]))
        query = state.get("query", "")
        all_items: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []

        for branch in branch_results:
            domain = branch.get("domain", "unknown")
            for item in branch.get("items", []):
                all_items.append({"domain": domain, **item})
                if "citation_id" in item:
                    citations.append(
                        {
                            "id": item["citation_id"],
                            "domain": domain,
                            "title": item.get("title") or item.get("name", ""),
                        }
                    )

        if synthesizer is not None:
            try:
                briefing = synthesizer(all_items, query)
            except Exception as exc:  # noqa: BLE001
                briefing = f"Synthesis error: {exc}"
                all_errors.append(briefing)
        elif all_items:
            lines = [f"Briefing for query: '{query}'"]
            lines.append(f"Total evidence items collected: {len(all_items)}")
            for item in all_items:
                title = item.get("title") or item.get("name", "Document")
                lines.append(f"- [{item['domain'].upper()}] {title}")
            briefing = "\n".join(lines)
        else:
            if all_errors:
                briefing = f"Search encountered errors: {'; '.join(all_errors)}."
            else:
                briefing = f"No documents found across searched domains for '{query}'."

        # H6: Distinguish empty vs error. Propagate partial_error when any branch errored
        if all_errors:
            if not all_items:
                final_status = "failed"
            else:
                final_status = "partial_error"
        else:
            final_status = "completed"

        output = {
            "briefing": briefing,
            "citations": citations,
            "evidence_count": len(all_items),
            "error_count": len(all_errors),
            "status": final_status,
        }
        return {
            "briefing": briefing,
            "citations": citations,
            "output": output,
            "status": final_status,
        }

    builder: StateGraph[WorkflowState, Any, Any, Any] = StateGraph(WorkflowState)
    builder.add_node("prepare_search", _prepare_search)
    builder.add_node("rag_search", _rag_search)
    builder.add_node("drive_search", _drive_search)
    builder.add_node("synthesize_briefing", _synthesize_briefing)

    builder.set_entry_point("prepare_search")
    builder.add_conditional_edges(
        "prepare_search",
        _dispatch_parallel,
        ["rag_search", "drive_search"],
    )
    builder.add_edge("rag_search", "synthesize_briefing")
    builder.add_edge("drive_search", "synthesize_briefing")
    builder.add_edge("synthesize_briefing", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()

"""Static and deterministic workflow contracts with graph structure and reachability validation."""

from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowNode(BaseModel):
    """An individual execution node in a static or hardened workflow graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        ...,
        description="Unique identifier for the node in the workflow graph.",
    )
    node_type: str = Field(
        ...,
        description="Node execution type (e.g. 'deterministic', 'llm_synthesis', 'tool_call', 'decision').",
    )
    assigned_agent: str | None = Field(
        default=None,
        description="Agent assigned to execute this node if applicable.",
    )
    action: str = Field(
        ...,
        description="Action, tool, or handler function to invoke at this node.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Static configuration and parameter bindings for this node.",
    )


class WorkflowEdge(BaseModel):
    """Directed connection between workflow nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_node_id: str = Field(
        ...,
        description="Origin node ID.",
    )
    target_node_id: str = Field(
        ...,
        description="Destination node ID.",
    )
    condition: str | None = Field(
        default=None,
        description="Optional routing condition or predicate expression to follow this edge.",
    )


class WorkflowDefinition(BaseModel):
    """Specification of a deterministic DAG workflow with full reachability and zero-incoming-edge entry roots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        description="Unique workflow name (e.g. 'meeting_prep_hardened').",
    )
    description: str = Field(
        ...,
        description="Description of what this workflow orchestrates.",
    )
    entry_node_ids: list[str] = Field(
        ...,
        min_length=1,
        description="List of entry point node IDs where workflow execution begins.",
    )
    nodes: list[WorkflowNode] = Field(
        ...,
        min_length=1,
        description="List of nodes forming the workflow graph.",
    )
    edges: list[WorkflowEdge] = Field(
        default_factory=list,
        description="Directed edges connecting the nodes.",
    )

    @field_validator("entry_node_ids", mode="before")
    @classmethod
    def coerce_entry_nodes(cls, v: Any) -> list[str]:
        """Support single string or list of entry node IDs."""
        if isinstance(v, str):
            return [v]
        return v

    @property
    def entry_node_id(self) -> str:
        """Convenience accessor for primary entry node."""
        return self.entry_node_ids[0]

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        """Validate node uniqueness, edge validity, entry roots with 0 in-degree, cycle absence, and reachability."""
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError(f"Duplicate node IDs found in workflow '{self.name}'.")

        # Reject duplicate entry_node_ids
        if len(set(self.entry_node_ids)) != len(self.entry_node_ids):
            raise ValueError(f"Duplicate entry_node_ids found in workflow '{self.name}'.")

        # Validate all entry nodes exist
        entry_set = set(self.entry_node_ids)
        for entry_id in self.entry_node_ids:
            if entry_id not in node_ids:
                raise ValueError(f"Entry node '{entry_id}' does not exist in workflow nodes.")

        # Validate edges
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                raise ValueError(
                    f"Edge source '{edge.source_node_id}' does not exist in workflow nodes."
                )
            if edge.target_node_id not in node_ids:
                raise ValueError(
                    f"Edge target '{edge.target_node_id}' does not exist in workflow nodes."
                )
            if edge.source_node_id == edge.target_node_id:
                raise ValueError(f"Self-loop detected on node '{edge.source_node_id}'.")

        # Compute in-degree and adjacency
        in_degree = dict.fromkeys(node_ids, 0)
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for edge in self.edges:
            in_degree[edge.target_node_id] += 1
            adj[edge.source_node_id].append(edge.target_node_id)

        # Enforce that entry nodes must have zero incoming edges
        for entry_id in self.entry_node_ids:
            if in_degree[entry_id] > 0:
                raise ValueError(
                    f"Entry node '{entry_id}' cannot have incoming edges (in-degree is {in_degree[entry_id]})."
                )

        # Enforce that all nodes with zero incoming edges must be declared as entry nodes
        for nid, deg in in_degree.items():
            if deg == 0 and nid not in entry_set:
                raise ValueError(
                    f"Root node '{nid}' has 0 incoming edges and must be declared in entry_node_ids."
                )

        # Cycle detection using Kahn's algorithm
        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited_count != len(self.nodes):
            raise ValueError(f"Cycle detected in workflow graph for '{self.name}'.")

        # Reachability validation: all nodes must be reachable from entry_node_ids
        reachable: set[str] = set()
        traverse_queue: deque[str] = deque(self.entry_node_ids)

        while traverse_queue:
            curr = traverse_queue.popleft()
            if curr not in reachable:
                reachable.add(curr)
                for nxt in adj[curr]:
                    if nxt not in reachable:
                        traverse_queue.append(nxt)

        unreachable = node_ids - reachable
        if unreachable:
            raise ValueError(
                f"Unreachable node(s) found in workflow '{self.name}': {sorted(unreachable)}. "
                f"All nodes must be reachable from entry nodes {self.entry_node_ids}."
            )

        return self


class WorkflowExecutionResult(BaseModel):
    """Overall outcome of a workflow execution run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_name: str = Field(
        ...,
        description="Name of the executed workflow.",
    )
    success: bool = Field(
        ...,
        description="Whether the workflow execution succeeded completely.",
    )
    executed_node_ids: list[str] = Field(
        default_factory=list,
        description="Sequence of node IDs executed.",
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated outputs from workflow nodes.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Errors encountered during execution.",
    )

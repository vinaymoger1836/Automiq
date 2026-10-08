"""Versioned workflow graph contract and publish-time validation."""

import re
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualConfig(StrictModel):
    pass


class HttpConfig(StrictModel):
    # Phase 2 executes the deterministic mock adapter. Real egress is disabled.
    operation: Literal["mock"] = "mock"
    mock_output: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    failures_before_success: int = Field(default=0, ge=0, le=2)
    permanent_failure: bool = False
    delay_seconds: int = Field(default=0, ge=0, le=6)
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class Expression(StrictModel):
    path: str = Field(min_length=1, max_length=200)
    operator: Literal["eq", "ne", "exists"]
    value: str | int | float | bool | None = None


class ConditionConfig(StrictModel):
    expression: Expression


class EndConfig(StrictModel):
    pass


class Position(StrictModel):
    x: float = Field(ge=-10_000, le=10_000)
    y: float = Field(ge=-10_000, le=10_000)


class ManualNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["trigger.manual"]
    config: ManualConfig
    position: Position | None = None


class HttpNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["action.http"]
    config: HttpConfig
    position: Position | None = None


class ConditionNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["condition"]
    config: ConditionConfig
    position: Position | None = None


class EndNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["end"]
    config: EndConfig
    position: Position | None = None


Node = Annotated[ManualNode | HttpNode | ConditionNode | EndNode, Field(discriminator="type")]


class Edge(StrictModel):
    source: str
    target: str
    source_handle: Literal["true", "false"] | None = None


class Graph(StrictModel):
    schema_version: Literal["1.0"]
    nodes: list[Node] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(max_length=150)

    @model_validator(mode="after")
    def bound_size(self) -> "Graph":
        # JSONB payloads and Temporal histories must stay bounded.
        if len(self.model_dump_json()) > 64_000:
            raise ValueError("graph exceeds 64 KB")
        return self


class Diagnostic(StrictModel):
    code: str
    message: str
    node_id: str | None = None


def validate_graph(graph: Graph) -> list[Diagnostic]:
    errors: list[Diagnostic] = []

    def add(code: str, message: str, node_id: str | None = None) -> None:
        errors.append(Diagnostic(code=code, message=message, node_id=node_id))

    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        add("duplicate_node", "Node IDs must be unique")
    triggers = [node for node in graph.nodes if node.type == "trigger.manual"]
    if len(triggers) != 1:
        add("trigger_count", "Graph must contain exactly one manual trigger")
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    incoming: dict[str, list[Edge]] = defaultdict(list)
    seen_edges: set[tuple[str, str, str | None]] = set()
    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes:
            add("unknown_endpoint", "Edge references a missing node")
            continue
        key = (edge.source, edge.target, edge.source_handle)
        if key in seen_edges:
            add("duplicate_edge", "Duplicate edge", edge.source)
        seen_edges.add(key)
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)
    for node in graph.nodes:
        ins, outs = incoming[node.id], outgoing[node.id]
        if node.type == "trigger.manual":
            if ins:
                add("trigger_incoming", "Trigger cannot have incoming edges", node.id)
        elif len(ins) != 1:
            add("incoming_count", "Each non-trigger node needs one incoming edge", node.id)
        if node.type == "end":
            if outs:
                add("end_outgoing", "End nodes cannot have outgoing edges", node.id)
        elif node.type == "condition":
            if len(outs) != 2 or {edge.source_handle for edge in outs} != {"true", "false"}:
                add("condition_branches", "Condition needs true and false branches", node.id)
        elif len(outs) != 1 or outs[0].source_handle is not None:
            add("outgoing_count", "Node needs one unlabeled outgoing edge", node.id)
        if node.type != "condition" and any(edge.source_handle is not None for edge in outs):
            add("invalid_handle", "Only conditions may have branch handles", node.id)

    visited: set[str] = set()
    active: set[str] = set()
    has_cycle = False

    def visit(node_id: str) -> None:
        nonlocal has_cycle
        if node_id in active:
            has_cycle = True
            return
        if node_id in visited:
            return
        active.add(node_id)
        for edge in outgoing[node_id]:
            visit(edge.target)
        active.remove(node_id)
        visited.add(node_id)

    if triggers:
        visit(triggers[0].id)
    if has_cycle:
        add("cycle", "Graph must be acyclic")
    for node in graph.nodes:
        if node.id not in visited:
            add("unreachable", "Node is not reachable from trigger", node.id)
        if isinstance(node, ConditionNode):
            path = node.config.expression.path
            match = re.fullmatch(
                r"steps\.([A-Za-z][A-Za-z0-9_-]{0,63})\.output(?:\.[A-Za-z][A-Za-z0-9_-]{0,63})*",
                path,
            )
            trigger_path = re.fullmatch(r"trigger\.payload(?:\.[A-Za-z][A-Za-z0-9_-]{0,63})*", path)
            if not trigger_path and not match:
                add(
                    "invalid_path",
                    "Expression path must reference trigger payload or step output",
                    node.id,
                )
            elif match:
                source = match.group(1)
                if source not in nodes or source == node.id:
                    add(
                        "invalid_reference",
                        "Expression references an unknown or current step",
                        node.id,
                    )
                else:
                    # Follow the sole incoming chain to establish ancestry.
                    current = node.id
                    ancestors: set[str] = set()
                    while len(incoming[current]) == 1 and current not in ancestors:
                        ancestors.add(current)
                        current = incoming[current][0].source
                    if source not in ancestors and source != current:
                        add("non_upstream_reference", "Expression step must be upstream", node.id)
    return errors


EMPTY_GRAPH = Graph(
    schema_version="1.0",
    nodes=[
        ManualNode(id="start", type="trigger.manual", config=ManualConfig()),
        EndNode(id="end", type="end", config=EndConfig()),
    ],
    edges=[Edge(source="start", target="end")],
)

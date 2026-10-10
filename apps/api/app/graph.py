"""Versioned workflow graph contract and publish-time validation."""

import re
import uuid
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualConfig(StrictModel):
    pass


class GithubIssueConfig(StrictModel):
    pass


class ScheduleConfig(StrictModel):
    pass


class HttpConfig(StrictModel):
    operation: Literal["mock", "https_get"] = "mock"
    mock_output: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    failures_before_success: int = Field(default=0, ge=0, le=2)
    permanent_failure: bool = False
    delay_seconds: int = Field(default=0, ge=0, le=6)
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    path: str | None = Field(default=None, max_length=160)
    response_fields: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_operation(self) -> "HttpConfig":
        if self.operation == "https_get":
            if not self.path or not re.fullmatch(
                r"/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?", self.path
            ):
                raise ValueError("HTTPS GET requires a simple relative path")
            if (
                self.mock_output
                or self.failures_before_success
                or self.permanent_failure
                or self.delay_seconds
            ):
                raise ValueError("HTTPS GET cannot include mock behavior")
            if len(set(self.response_fields)) != len(self.response_fields) or any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", field)
                or field == "http_status"
                or any(
                    word in field.lower()
                    for word in ("secret", "token", "password", "credential", "authorization")
                )
                for field in self.response_fields
            ):
                raise ValueError("Response fields must be unique non-sensitive scalar keys")
        elif self.path is not None or self.response_fields:
            raise ValueError("Mock action cannot specify an HTTPS request")
        return self


class Expression(StrictModel):
    path: str = Field(min_length=1, max_length=200)
    operator: Literal["eq", "ne", "exists"]
    value: str | int | float | bool | None = None


class ConditionConfig(StrictModel):
    expression: Expression


class EndConfig(StrictModel):
    pass


class GithubCommentConfig(StrictModel):
    integration_id: uuid.UUID
    body: str = Field(min_length=1, max_length=4000)


class SlackMessageConfig(StrictModel):
    integration_id: uuid.UUID
    channel: str = Field(pattern=r"^[#A-Za-z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=4000)


class AgentSchema(StrictModel):
    # Deliberately small JSON Schema subset so the same contract works for fake and real providers.
    type: Literal["object"] = "object"
    properties: dict[str, Literal["string", "number", "integer", "boolean"]] = Field(
        default_factory=dict, max_length=12
    )
    required: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_fields(self) -> "AgentSchema":
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", key) for key in self.properties):
            raise ValueError("Agent schema field names must be simple identifiers")
        if (
            len(set(self.required)) != len(self.required)
            or not set(self.required) <= self.properties.keys()
        ):
            raise ValueError("Agent required fields must be unique declared properties")
        return self


class AgentConfig(StrictModel):
    model_profile: Literal["fake", "openai"] = "fake"
    instructions: str = Field(min_length=1, max_length=2000)
    allowed_tools: list[Literal["github.search_issues"]] = Field(default_factory=list, max_length=3)
    github_integration_id: uuid.UUID | None = None
    input_schema: AgentSchema = Field(default_factory=AgentSchema)
    output_schema: AgentSchema = Field(
        default_factory=lambda: AgentSchema(
            properties={"severity": "string", "reason": "string"}, required=["severity", "reason"]
        )
    )
    max_tool_calls: int = Field(default=1, ge=0, le=5)
    max_duration_seconds: int = Field(default=30, ge=1, le=120)
    max_input_tokens: int = Field(default=2000, ge=100, le=8000)
    max_output_tokens: int = Field(default=300, ge=30, le=2000)
    max_cost_microusd: int = Field(default=100000, ge=0, le=1000000)

    @model_validator(mode="after")
    def valid_tools(self) -> "AgentConfig":
        if self.output_schema.properties != {"severity": "string", "reason": "string"} or set(
            self.output_schema.required
        ) != {"severity", "reason"}:
            raise ValueError("Phase 5 agent output must declare severity and reason")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("Agent tools must be unique")
        if self.allowed_tools and self.github_integration_id is None:
            raise ValueError("GitHub tool requires a configured integration")
        if self.max_tool_calls == 0 and self.allowed_tools:
            raise ValueError("Tool quota must allow configured tools")
        return self


class ApprovalConfig(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)


class Position(StrictModel):
    x: float = Field(ge=-10_000, le=10_000)
    y: float = Field(ge=-10_000, le=10_000)


class ManualNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["trigger.manual"]
    config: ManualConfig
    position: Position | None = None


class GithubIssueNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["trigger.github_issue"]
    config: GithubIssueConfig
    position: Position | None = None


class ScheduleNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["trigger.schedule"]
    config: ScheduleConfig
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


class GithubCommentNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["action.github_comment"]
    config: GithubCommentConfig
    position: Position | None = None


class SlackMessageNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["action.slack_message"]
    config: SlackMessageConfig
    position: Position | None = None


class AgentNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["agent"]
    config: AgentConfig
    position: Position | None = None


class ApprovalNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: Literal["approval"]
    config: ApprovalConfig
    position: Position | None = None


Node = Annotated[
    ManualNode
    | GithubIssueNode
    | ScheduleNode
    | HttpNode
    | GithubCommentNode
    | SlackMessageNode
    | AgentNode
    | ApprovalNode
    | ConditionNode
    | EndNode,
    Field(discriminator="type"),
]


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
    triggers = [node for node in graph.nodes if node.type.startswith("trigger.")]
    if len(triggers) != 1:
        add("trigger_count", "Graph must contain exactly one trigger")
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
        if node.type.startswith("trigger."):
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
        if node.type in {"action.github_comment", "action.slack_message"}:
            current = node.id
            has_approval = False
            seen: set[str] = set()
            while len(incoming[current]) == 1 and current not in seen:
                seen.add(current)
                current = incoming[current][0].source
                upstream = nodes[current]
                if upstream.type == "approval":
                    has_approval = True
                if upstream.type == "agent" and not has_approval:
                    add(
                        "approval_required",
                        "External actions influenced by an agent need an approval checkpoint",
                        node.id,
                    )
                    break

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

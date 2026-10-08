"use client";

import { useCallback, useEffect, useMemo, useRef, type DragEvent } from "react";
import {
  ReactFlow,
  Background, Controls, Handle, MiniMap, Position, applyEdgeChanges, type Connection,
  type Edge, type EdgeChange, type Node, type NodeChange, type NodeProps,
  useReactFlow, ReactFlowProvider,
} from "@xyflow/react";
import type { Graph, GraphEdge, GraphNode, NodeKind, Run } from "@/lib/studio-api";

const labels: Record<NodeKind, string> = {
  "trigger.manual": "Manual trigger", "action.http": "HTTP action", condition: "Condition", end: "End",
};
const initials: Record<NodeKind, string> = {
  "trigger.manual": "TR", "action.http": "HT", condition: "IF", end: "EN",
};

type FlowData = { label: string; kind: NodeKind; status: string; selected: boolean } & Record<string, unknown>;
type FlowNode = Node<FlowData, "workflow">;

function WorkflowNode({ data }: NodeProps<FlowNode>) {
  return <div className={`flow-node flow-${data.kind.replace(".", "-")} state-${data.status} ${data.selected ? "selected" : ""}`}>
    {data.kind !== "trigger.manual" && <Handle type="target" position={Position.Left} />}
    <div className="flow-node-top"><span className="flow-node-icon">{initials[data.kind]}</span><span className="flow-node-kind">{data.kind.replace(".", " / ")}</span></div>
    <strong>{data.label}</strong>
    <span className="flow-node-state">{data.status === "idle" ? "Ready to configure" : data.status}</span>
    {data.kind === "condition" ? <>
      <Handle type="source" id="true" position={Position.Right} style={{ top: "32%" }} />
      <Handle type="source" id="false" position={Position.Right} style={{ top: "70%" }} />
      <span className="handle-label true">TRUE</span><span className="handle-label false">FALSE</span>
    </> : data.kind !== "end" && <Handle type="source" position={Position.Right} />}
  </div>;
}

const nodeTypes = { workflow: WorkflowNode };

function newNode(kind: NodeKind, graph: Graph, position: { x: number; y: number }): GraphNode {
  const stem = kind === "trigger.manual" ? "start" : kind === "action.http" ? "action" : kind === "condition" ? "route" : "end";
  let index = 1;
  while (graph.nodes.some((node) => node.id === `${stem}_${index}`)) index++;
  const config = kind === "condition" ? { expression: { path: "trigger.payload.flag", operator: "eq", value: true } } :
    kind === "action.http" ? { operation: "mock", mock_output: { ok: true } } : {};
  return { id: `${stem}_${index}`, type: kind, config, position };
}

function edgeIssue(graph: Graph, source: string, target: string, handle?: string | null): string | null {
  const from = graph.nodes.find((node) => node.id === source);
  const to = graph.nodes.find((node) => node.id === target);
  if (!from || !to || source === target) return "Choose two different nodes.";
  if (from.type === "end" || to.type === "trigger.manual") return "This node cannot have that connection.";
  if (graph.edges.some((edge) => edge.target === target)) return "Each node can have only one incoming edge.";
  if (from.type === "condition") {
    if (handle !== "true" && handle !== "false") return "Choose the true or false branch.";
    if (graph.edges.some((edge) => edge.source === source && edge.source_handle === handle)) return "That branch is already connected.";
  } else if (graph.edges.some((edge) => edge.source === source)) return "This node already has an outgoing edge.";
  const stack = [target];
  const seen = new Set<string>();
  while (stack.length) {
    const current = stack.pop()!;
    if (current === source) return "Cycles are not supported.";
    if (seen.has(current)) continue;
    seen.add(current);
    stack.push(...graph.edges.filter((edge) => edge.source === current).map((edge) => edge.target));
  }
  return null;
}

type Props = {
  graph: Graph; onChange: (graph: Graph) => void; onSelect: (id: string | null) => void;
  selectedId: string | null; readOnly: boolean; run: Run | null; theme: "light" | "dark";
  onNotice: (message: string) => void;
};

function CanvasInner({ graph, onChange, onSelect, selectedId, readOnly, run, theme, onNotice }: Props) {
  const { screenToFlowPosition, fitView } = useReactFlow();
  const canvasRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      requestAnimationFrame(() => { void fitView({ padding: 0.25, duration: 0 }); });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [fitView]);
  useEffect(() => {
    requestAnimationFrame(() => { void fitView({ padding: 0.25, duration: 0 }); });
  }, [graph.nodes.length, fitView]);
  const nodes = useMemo<FlowNode[]>(() => graph.nodes.map((node, index) => {
    const latest = run?.steps.filter((step) => step.node_id === node.id).at(-1);
    return {
      id: node.id, type: "workflow",
      position: node.position || { x: 60 + index * 250, y: 170 + (index % 2) * 32 },
      measured: { width: 190, height: 102 },
      data: { label: labels[node.type], kind: node.type, status: latest?.status || "idle", selected: node.id === selectedId },
      draggable: !readOnly, selectable: true,
    };
  }), [graph.nodes, readOnly, run?.steps, selectedId]);
  const edges = useMemo<Edge[]>(() => graph.edges.map((edge) => ({
    id: `${edge.source}:${edge.source_handle || "default"}:${edge.target}`,
    source: edge.source, target: edge.target, sourceHandle: edge.source_handle || undefined,
    label: edge.source_handle || undefined, type: "smoothstep", animated: run?.status === "running",
  })), [graph.edges, run?.status]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    if (readOnly) return;
    const updates = changes.filter((change) => change.type === "position" && change.position);
    if (!updates.length) return;
    onChange({ ...graph, nodes: graph.nodes.map((node) => {
      const change = updates.find((item) => item.type === "position" && item.id === node.id);
      return change && change.type === "position" && change.position ? { ...node, position: change.position } : node;
    }) });
  }, [graph, onChange, readOnly]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (readOnly) return;
    const updated = applyEdgeChanges(changes, edges);
    const ids = new Set(updated.map((edge) => edge.id));
    if (ids.size !== edges.length) onChange({ ...graph, edges: graph.edges.filter((edge) =>
      ids.has(`${edge.source}:${edge.source_handle || "default"}:${edge.target}`)) });
  }, [edges, graph, onChange, readOnly]);

  const onConnect = useCallback((connection: Connection) => {
    if (readOnly || !connection.source || !connection.target) return;
    const issue = edgeIssue(graph, connection.source, connection.target, connection.sourceHandle);
    if (issue) { onNotice(issue); return; }
    const edge: GraphEdge = { source: connection.source, target: connection.target,
      ...(connection.sourceHandle ? { source_handle: connection.sourceHandle as "true" | "false" } : {}) };
    onChange({ ...graph, edges: [...graph.edges, edge] });
    onNotice("Connection added. Save the draft to keep it.");
  }, [graph, onChange, onNotice, readOnly]);

  const onDrop = useCallback((event: DragEvent) => {
    event.preventDefault();
    if (readOnly) return;
    const kind = event.dataTransfer.getData("application/automiq-node") as NodeKind;
    if (!Object.hasOwn(labels, kind)) return;
    if (kind === "trigger.manual" && graph.nodes.some((node) => node.type === kind)) {
      onNotice("A workflow can have only one manual trigger."); return;
    }
    onChange({ ...graph, nodes: [...graph.nodes, newNode(kind, graph,
      screenToFlowPosition({ x: event.clientX, y: event.clientY }))] });
  }, [graph, onChange, onNotice, readOnly, screenToFlowPosition]);

  return <div ref={canvasRef} className="canvas-wrap" onDrop={onDrop} onDragOver={(event) => event.preventDefault()}>
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} colorMode={theme}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
      onNodeClick={(_, node) => onSelect(node.id)} onPaneClick={() => onSelect(null)}
      nodesDraggable={!readOnly} nodesConnectable={!readOnly} edgesReconnectable={false}
      deleteKeyCode={readOnly ? null : "Delete"} fitView fitViewOptions={{ padding: 0.25 }}
      minZoom={0.2}
      proOptions={{ hideAttribution: true }}>
      <Background gap={20} size={1} /><Controls showInteractive={false} />
      <MiniMap pannable zoomable nodeColor={theme === "dark" ? "#9ce4b8" : "#0e604d"}
        maskColor={theme === "dark" ? "#10171388" : "#f5f4f088"} />
    </ReactFlow>
  </div>;
}

export function WorkflowCanvas(props: Props) {
  return <ReactFlowProvider><CanvasInner {...props} /></ReactFlowProvider>;
}

export { edgeIssue, labels, newNode };

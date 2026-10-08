export type Role = "owner" | "editor" | "viewer";
export type NodeKind = "trigger.manual" | "action.http" | "condition" | "end";
export type Scalar = string | number | boolean | null;
export type GraphNode = {
  id: string;
  type: NodeKind;
  config: Record<string, unknown>;
  position?: { x: number; y: number } | null;
};
export type GraphEdge = { source: string; target: string; source_handle?: "true" | "false" | null };
export type Graph = { schema_version: "1.0"; nodes: GraphNode[]; edges: GraphEdge[] };
export type Diagnostic = { code: string; message: string; node_id?: string | null };
export type Workspace = { id: string; name: string; slug: string; role: Role };
export type Me = { id: string; email: string; csrf_token: string; workspaces: Workspace[] };
export type Workflow = {
  id: string; workspace_id: string; name: string; status: "active" | "archived";
  draft_graph: Graph; draft_revision: number; published_version_id: string | null; updated_at: string;
};
export type Version = { id: string; workflow_id: string; version: number; checksum: string; published_at: string; graph: Graph };
export type Step = { node_id: string; attempt: number; status: string; output: Record<string, unknown> | null; error: string | null; started_at: string; finished_at: string | null };
export type Run = { run_id: string; workflow_id: string; version_id: string; status: string; input: Record<string, unknown>; error: string | null; created_at: string; started_at: string | null; completed_at: string | null; steps: Step[] };

export class ApiError extends Error {
  constructor(public status: number, message: string, public diagnostics: Diagnostic[] = []) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  const data: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = typeof data === "object" && data !== null && "error" in data ?
      (data as { error: { message?: string; diagnostics?: Diagnostic[] } }).error : null;
    throw new ApiError(response.status, error?.message || `Request failed (${response.status})`, error?.diagnostics || []);
  }
  return data as T;
}

export function pathFor(workspaceId: string, workflowId?: string): string {
  const base = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/workflows`;
  return workflowId ? `${base}/${encodeURIComponent(workflowId)}` : base;
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import type { Diagnostic, Graph, GraphNode, Me, NodeKind, Role, Run, Version, Workflow, Workspace } from "@/lib/studio-api";
import { ApiError, api, pathFor } from "@/lib/studio-api";
import { WorkflowCanvas, edgeIssue, labels, newNode } from "./workflow-canvas";
import "./studio.css";

type RunSummary = { run_id: string; version_id: string; status: string; created_at: string };
type TimelineEvent = { id: number; type: string; payload: Record<string, unknown> };
type Notice = { kind: "success" | "error" | "info"; text: string };
type Member = { user_id: string; email: string; role: Role };
type Integration = { id: string; provider: "github" | "slack"; display_name: string; key_version: number; credential_version: number; revoked_at: string | null; can_configure: boolean };
type Trigger = { id: string; type: "github.issue" | "schedule"; version_id: string; integration_id: string | null; enabled: boolean; webhook_path: string | null; webhook_url: string | null; config: Record<string, string> };
type Approval = { id: string; run_id: string; node_id: string; title: string; status: string; created_at: string; expires_at: string; decided_at: string | null; can_approve: boolean };

const palette: { kind: NodeKind; hint: string }[] = [
  { kind: "trigger.manual", hint: "Start here" },
  { kind: "trigger.github_issue", hint: "Signed webhook" },
  { kind: "trigger.schedule", hint: "Timed run" },
  { kind: "action.http", hint: "Mock action" },
  { kind: "action.github_comment", hint: "Reply to issue" },
  { kind: "action.slack_message", hint: "Notify a channel" },
  { kind: "agent", hint: "Bounded classification" },
  { kind: "approval", hint: "Human checkpoint" },
  { kind: "condition", hint: "True / false" },
  { kind: "end", hint: "Finish a path" },
];

const starterGraph: Graph = {
  schema_version: "1.0",
  nodes: [
    { id: "start", type: "trigger.manual", config: {}, position: { x: 80, y: 180 } },
    { id: "action_1", type: "action.http", config: { operation: "mock", mock_output: { ok: true } }, position: { x: 380, y: 180 } },
    { id: "end", type: "end", config: {}, position: { x: 680, y: 180 } },
  ],
  edges: [{ source: "start", target: "action_1" }, { source: "action_1", target: "end" }],
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function parsePayload(text: string): Record<string, string | number | boolean | null> {
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("Run input must be a JSON object.");
  if (Object.values(parsed).some((value) => value !== null && !["string", "number", "boolean"].includes(typeof value))) {
    throw new Error("Run input values must be text, numbers, booleans, or null.");
  }
  return parsed as Record<string, string | number | boolean | null>;
}

export default function StudioPage() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [me, setMe] = useState<Me | null>(null);
  const [authState, setAuthState] = useState<"loading" | "signed_out" | "signed_in">("loading");
  const [devIdentity, setDevIdentity] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connection, setConnection] = useState<"idle" | "live" | "polling" | "complete">("idle");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [busy, setBusy] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workflowName, setWorkflowName] = useState("");
  const [workflowTemplate, setWorkflowTemplate] = useState<"blank" | "starter">("blank");
  const [members, setMembers] = useState<Member[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [newProvider, setNewProvider] = useState<"github" | "slack">("github");
  const [editIntegrationId, setEditIntegrationId] = useState("");
  const [assignmentTarget, setAssignmentTarget] = useState("");
  const [assignmentEditor, setAssignmentEditor] = useState("");
  const [integrationName, setIntegrationName] = useState("");
  const [integrationToken, setIntegrationToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [repository, setRepository] = useState("");
  const [triggerIntegration, setTriggerIntegration] = useState("");
  const [scheduleCron, setScheduleCron] = useState("0 9 * * 1-5");
  const [scheduleTimezone, setScheduleTimezone] = useState("UTC");
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<Role>("viewer");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [archiveTarget, setArchiveTarget] = useState<string | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [runInput, setRunInput] = useState('{"flag": true}');
  const [configText, setConfigText] = useState("");
  const [linkSource, setLinkSource] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkBranch, setLinkBranch] = useState<"true" | "false">("true");
  const timelineRef = useRef<HTMLOListElement>(null);

  const workspace = me?.workspaces.find((item) => item.id === workspaceId) || null;
  const role: Role | null = workspace?.role || null;
  const canEdit = (role === "owner" || role === "editor") && selected?.status !== "archived";
  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) || null;
  const base = selected && workspaceId ? pathFor(workspaceId, selected.id) : null;

  const announce = useCallback((text: string, kind: Notice["kind"] = "info") => setNotice({ text, kind }), []);

  const loadMe = useCallback(async () => {
    try {
      const identity = await api<Me>("/api/v1/me");
      setMe(identity);
      setWorkspaceId((current) => current && identity.workspaces.some((item) => item.id === current) ?
        current : identity.workspaces[0]?.id || null);
      setAuthState("signed_in");
    } catch (error) {
      setAuthState("signed_out");
      if (!(error instanceof ApiError && error.status === 401)) announce(errorText(error), "error");
    }
  }, [announce]);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
    void loadMe();
    void api<{ dev_identity: boolean }>("/auth/modes").then((modes) => setDevIdentity(modes.dev_identity)).catch(() => {});
  }, [loadMe]);

  const openWorkflow = useCallback(async (ws: string, id: string) => {
    try {
      const [workflow, published, runs, bindings] = await Promise.all([
        api<Workflow>(pathFor(ws, id)),
        api<Version[]>(`${pathFor(ws, id)}/versions`),
        api<RunSummary[]>(`${pathFor(ws, id)}/runs`),
        api<Trigger[]>(`${pathFor(ws, id)}/triggers`),
      ]);
      setSelected(workflow); setGraph(workflow.draft_graph); setDirty(false); setSelectedNodeId(null);
      setLinkSource(""); setLinkTarget("");
      setDiagnostics([]); setVersions(published); setRecentRuns(runs); setTriggers(bindings);
      setRunId(runs[0]?.run_id || null); setRun(null); setTimeline([]);
    } catch (error) { announce(errorText(error), "error"); }
  }, [announce]);

  useEffect(() => {
    if (!workspaceId || authState !== "signed_in") { setWorkflows([]); setSelected(null); setGraph(null); return; }
    let active = true;
    void api<Workflow[]>(pathFor(workspaceId)).then((items) => {
      if (!active) return;
      setWorkflows(items);
      const first = items.find((item) => item.status === "active") || items[0];
      if (first) void openWorkflow(workspaceId, first.id);
      else { setSelected(null); setGraph(null); setRunId(null); }
    }).catch((error) => announce(errorText(error), "error"));
    return () => { active = false; };
  }, [workspaceId, authState, openWorkflow, announce]);

  useEffect(() => {
    if (!workspaceId || role !== "owner") { setMembers([]); return; }
    void api<Member[]>(`/api/v1/workspaces/${workspaceId}/memberships`).then(setMembers)
      .catch((error) => announce(errorText(error), "error"));
  }, [workspaceId, role, announce]);

  useEffect(() => {
    if (!workspaceId || authState !== "signed_in") { setIntegrations([]); return; }
    void api<Integration[]>(`/api/v1/workspaces/${workspaceId}/integrations`).then(setIntegrations)
      .catch((error) => announce(errorText(error), "error"));
  }, [workspaceId, authState, announce]);

  useEffect(() => {
    if (!workspaceId || authState !== "signed_in") { setApprovals([]); return; }
    const refresh = () => { void api<Approval[]>(`/api/v1/workspaces/${workspaceId}/approvals`)
      .then(setApprovals).catch((error) => announce(errorText(error), "error")); };
    refresh();
    const interval = window.setInterval(refresh, 5000);
    return () => window.clearInterval(interval);
  }, [workspaceId, authState, announce]);

  useEffect(() => {
    if (!base) return;
    const refreshRuns = () => { void api<RunSummary[]>(`${base}/runs`).then(setRecentRuns).catch(() => {}); };
    const interval = window.setInterval(refreshRuns, 5000);
    return () => window.clearInterval(interval);
  }, [base]);

  useEffect(() => {
    if (!dirty) return;
    const beforeLeave = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeLeave);
    return () => window.removeEventListener("beforeunload", beforeLeave);
  }, [dirty]);

  useEffect(() => {
    if (!runId || !workspaceId) { setRun(null); setTimeline([]); setConnection("idle"); return; }
    let active = true;
    let terminal = false;
    let cursor = 0;
    const controller = new AbortController();
    const runPath = `/api/v1/workspaces/${workspaceId}/runs/${runId}`;
    const refresh = async () => {
      try {
        const current = await api<Run>(runPath);
        if (!active) return;
        setRun(current);
        setRecentRuns((items) => items.map((item) => item.run_id === current.run_id ?
          { ...item, status: current.status } : item));
        terminal = current.status === "succeeded" || current.status === "failed";
        if (terminal) setConnection("complete");
      } catch (error) { if (active) announce(errorText(error), "error"); }
    };
    const watch = async () => {
      let first = true;
      while (active && (first || !terminal)) {
        first = false;
        try {
          const response = await fetch(`${runPath}/events`, {
            headers: cursor ? { "Last-Event-ID": String(cursor) } : {}, signal: controller.signal,
            cache: "no-store", credentials: "same-origin",
          });
          if (!response.ok || !response.body) throw new Error("Live events unavailable");
          setConnection("live");
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (active) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
            let boundary: number;
            while ((boundary = buffer.indexOf("\n\n")) >= 0) {
              const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
              const id = Number(block.match(/^id: (\d+)/m)?.[1]);
              const type = block.match(/^event: (.+)$/m)?.[1];
              const raw = block.match(/^data: (.+)$/m)?.[1];
              if (id && type && raw) {
                cursor = id;
                const payload = JSON.parse(raw) as Record<string, unknown>;
                setTimeline((items) => [...items, { id, type, payload }].slice(-100));
                void refresh();
                if (type === "run.succeeded" || type === "run.failed") terminal = true;
              }
            }
          }
        } catch {
          if (!active) return;
          setConnection("polling");
        }
        if (!terminal && active) await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      if (active) await refresh();
    };
    void refresh(); void watch();
    const poll = window.setInterval(() => { if (active && !terminal) void refresh(); }, 3000);
    return () => { active = false; controller.abort(); window.clearInterval(poll); };
  }, [runId, workspaceId, announce]);

  useEffect(() => {
    if (selectedNode?.type === "action.http") setConfigText(JSON.stringify(selectedNode.config.mock_output || {}, null, 2));
    else setConfigText("");
  }, [selectedNodeId, selected?.id, selectedNode?.type, selectedNode?.config.mock_output]);

  useEffect(() => {
    if (timelineRef.current) timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
  }, [timeline.length]);

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    try { await action(); } catch (error) {
      if (error instanceof ApiError && error.diagnostics.length) setDiagnostics(error.diagnostics);
      announce(errorText(error), "error");
    } finally { setBusy(false); }
  };

  const decideApproval = (item: Approval, decision: "approved" | "rejected") => void runAction(async () => {
    if (!workspaceId || !me || role !== "owner") return;
    const updated = await api<Approval>(`/api/v1/workspaces/${workspaceId}/approvals/${item.id}/decision`,
      { method: "POST", body: JSON.stringify({ decision }) }, me.csrf_token);
    setApprovals((rows) => rows.map((row) => row.id === item.id ? updated : row));
    announce(decision === "approved" ? "Action approved." : "Action rejected.", "success");
  });

  const changeGraph = (next: Graph) => { setGraph(next); setDirty(true); setDiagnostics([]); };
  const updateNode = (node: GraphNode) => {
    if (!graph) return;
    changeGraph({ ...graph, nodes: graph.nodes.map((item) => item.id === node.id ? node : item) });
  };
  const addNode = (kind: NodeKind) => {
    if (!graph || !canEdit) return;
    if (kind.startsWith("trigger.")) {
      const trigger = graph.nodes.find((node) => node.type.startsWith("trigger."));
      if (trigger) {
        changeGraph({ ...graph, nodes: graph.nodes.map((node) => node.id === trigger.id ? { ...node, type: kind, config: {} } : node) });
        setSelectedNodeId(trigger.id);
        announce(`${labels[kind]} selected. Save and publish the draft to activate it.`, "info");
        return;
      }
    }
    const isFirstAction = kind.startsWith("action.") && graph.nodes.length === 2 &&
      graph.nodes.some((node) => node.type.startsWith("trigger.")) &&
      graph.nodes.some((node) => node.type === "end");
    const next = newNode(kind, graph, isFirstAction ? { x: 310, y: 185 } :
      { x: 120 + graph.nodes.length * 215, y: 185 });
    const existing = isFirstAction ? graph.nodes.map((node) => node.type === "end" ?
      { ...node, position: { x: 560, y: 185 } } : node) : graph.nodes;
    changeGraph({ ...graph, nodes: [...existing, next] });
    setSelectedNodeId(next.id);
  };
  const addConnection = () => {
    if (!graph || !canEdit) return;
    const source = graph.nodes.find((node) => node.id === linkSource);
    const handle = source?.type === "condition" ? linkBranch : null;
    const issue = edgeIssue(graph, linkSource, linkTarget, handle);
    if (issue) { announce(issue, "error"); return; }
    changeGraph({ ...graph, edges: [...graph.edges, { source: linkSource, target: linkTarget,
      ...(handle ? { source_handle: handle } : {}) }] });
    announce("Connection added. Save the draft to keep it.", "success");
  };

  const signIn = (identity: "owner" | "editor" | "viewer") => void runAction(async () => {
    await api("/auth/dev", { method: "POST", body: JSON.stringify({ identity }) });
    await loadMe();
  });
  const switchTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("automiq-theme", next);
    setTheme(next);
  };
  const createWorkspace = () => void runAction(async () => {
    if (!me) return;
    const name = workspaceName.trim();
    const slug = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (!slug) throw new Error("Enter a workspace name with letters or numbers.");
    const created = await api<Workspace>("/api/v1/workspaces", { method: "POST",
      body: JSON.stringify({ name, slug }) }, me.csrf_token);
    setMe({ ...me, workspaces: [...me.workspaces, created] }); setWorkspaceId(created.id);
    setWorkspaceName(""); announce("Workspace created.", "success");
  });
  const createWorkflow = () => void runAction(async () => {
    if (!workspaceId || !me) return;
    const created = await api<Workflow>(pathFor(workspaceId), { method: "POST",
      body: JSON.stringify({ name: workflowName.trim() }) }, me.csrf_token);
    const workflow = workflowTemplate === "starter" ? await api<Workflow>(`${pathFor(workspaceId, created.id)}/draft`, {
      method: "PUT", body: JSON.stringify({ revision: created.draft_revision, graph: starterGraph }),
    }, me.csrf_token) : created;
    setWorkflows((items) => [workflow, ...items]); setWorkflowName(""); setWorkflowTemplate("blank");
    await openWorkflow(workspaceId, workflow.id);
    announce(workflowTemplate === "starter" ? "Starter draft is ready to publish or customize." : "Draft created. Add or connect nodes to begin.", "success");
  });
  const grantMember = () => void runAction(async () => {
    if (!workspaceId || !me) return;
    const granted = await api<Member>(`/api/v1/workspaces/${workspaceId}/memberships`, {
      method: "POST", body: JSON.stringify({ email: memberEmail.trim(), role: memberRole }),
    }, me.csrf_token);
    setMembers((items) => [...items.filter((item) => item.user_id !== granted.user_id), granted]);
    setMemberEmail(""); announce(`${granted.email} can now access this workspace.`, "success");
  });
  const save = () => void runAction(async () => {
    if (!base || !graph || !selected || !me) return;
    const saved = await api<Workflow>(`${base}/draft`, { method: "PUT",
      body: JSON.stringify({ revision: selected.draft_revision, graph }) }, me.csrf_token);
    setSelected(saved); setGraph(saved.draft_graph); setDirty(false);
    setWorkflows((items) => items.map((item) => item.id === saved.id ? saved : item));
    announce(`Draft saved at revision ${saved.draft_revision}.`, "success");
  });
  const validate = () => void runAction(async () => {
    if (!base || !graph || !me) return;
    const result = await api<{ valid: boolean; diagnostics: Diagnostic[] }>(`${base}/validate`,
      { method: "POST", body: JSON.stringify(graph) }, me.csrf_token);
    setDiagnostics(result.diagnostics);
    announce(result.valid ? "Graph is ready to publish." : `${result.diagnostics.length} validation issue(s) found.`,
      result.valid ? "success" : "error");
  });
  const publish = () => void runAction(async () => {
    if (!base || !selected || !me) return;
    if (dirty) throw new Error("Save the draft before publishing.");
    const version = await api<Version>(`${base}/publish`, { method: "POST",
      body: JSON.stringify({ revision: selected.draft_revision }) }, me.csrf_token);
    setVersions((items) => [version, ...items]); setSelected({ ...selected, published_version_id: version.id });
    setWorkflows((items) => items.map((item) => item.id === selected.id ?
      { ...item, published_version_id: version.id } : item));
    setConfirmPublish(false); setDiagnostics([]); announce(`Version ${version.version} published.`, "success");
  });
  const startRun = () => void runAction(async () => {
    if (!base || !me) return;
    const payload = parsePayload(runInput);
    const started = await api<{ run_id: string; status: string; version_id: string }>(`${base}/runs`, {
      method: "POST", headers: { "Idempotency-Key": `browser-${crypto.randomUUID()}` },
      body: JSON.stringify({ payload }),
    }, me.csrf_token);
    setRunId(started.run_id); setRun(null); setTimeline([]);
    setRecentRuns((items) => [{ run_id: started.run_id, version_id: started.version_id,
      status: started.status, created_at: new Date().toISOString() }, ...items]);
    announce("Run queued. Live progress will appear below.", "success");
  });
  const rename = () => void runAction(async () => {
    if (!renaming || !workspaceId || !me) return;
    const updated = await api<Workflow>(pathFor(workspaceId, renaming), { method: "PATCH",
      body: JSON.stringify({ name: renameText.trim() }) }, me.csrf_token);
    setWorkflows((items) => items.map((item) => item.id === updated.id ? updated : item));
    if (selected?.id === updated.id) setSelected(updated);
    setRenaming(null); announce("Workflow renamed.", "success");
  });
  const archive = () => void runAction(async () => {
    if (!archiveTarget || !workspaceId || !me) return;
    const updated = await api<Workflow>(`${pathFor(workspaceId, archiveTarget)}/archive`,
      { method: "POST" }, me.csrf_token);
    setWorkflows((items) => items.map((item) => item.id === updated.id ? updated : item));
    if (selected?.id === updated.id) setSelected(updated);
    setArchiveTarget(null); announce("Workflow archived.", "success");
  });
  const saveIntegration = () => void runAction(async () => {
    if (!workspaceId || !me) return;
    const body = { provider: newProvider, display_name: integrationName.trim(), token: integrationToken,
      ...(newProvider === "github" ? { webhook_secret: webhookSecret, repository: repository.trim() } : {}) };
    const path = `/api/v1/workspaces/${workspaceId}/integrations`;
    const saved = await api<Integration>(editIntegrationId ? `${path}/${editIntegrationId}` : path,
      { method: editIntegrationId ? "PUT" : "POST", body: JSON.stringify(body) }, me.csrf_token);
    setIntegrations((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
    setIntegrationToken(""); setWebhookSecret(""); setIntegrationName(""); setRepository(""); setEditIntegrationId("");
    announce(editIntegrationId ? "Credential rotated." : "Integration saved.", "success");
  });
  const revokeIntegration = (id: string) => void runAction(async () => {
    if (!workspaceId || !me) return;
    const updated = await api<Integration>(`/api/v1/workspaces/${workspaceId}/integrations/${id}/revoke`,
      { method: "POST" }, me.csrf_token);
    setIntegrations((items) => items.map((item) => item.id === id ? updated : item));
    announce("Integration revoked. Its triggers and actions will stop.", "success");
  });
  const assignIntegration = () => void runAction(async () => {
    if (!workspaceId || !me || !assignmentTarget || !assignmentEditor) return;
    await api<Integration>(`/api/v1/workspaces/${workspaceId}/integrations/${assignmentTarget}/assign`,
      { method: "POST", body: JSON.stringify({ user_id: assignmentEditor }) }, me.csrf_token);
    setAssignmentTarget(""); setAssignmentEditor("");
    announce("Editor assigned to integration.", "success");
  });
  const bindGithubTrigger = () => void runAction(async () => {
    if (!base || !me) return;
    const trigger = await api<Trigger>(`${base}/triggers/github`, { method: "POST",
      body: JSON.stringify({ integration_id: triggerIntegration }) }, me.csrf_token);
    setTriggers((items) => [...items, trigger]); announce("GitHub webhook is ready.", "success");
  });
  const bindSchedule = () => void runAction(async () => {
    if (!base || !me) return;
    const trigger = await api<Trigger>(`${base}/triggers/schedule`, { method: "POST",
      body: JSON.stringify({ cron: scheduleCron.trim(), timezone: scheduleTimezone.trim() }) }, me.csrf_token);
    setTriggers((items) => [...items, trigger]); announce("Schedule queued for registration.", "success");
  });
  const disableTrigger = (id: string) => void runAction(async () => {
    if (!base || !me) return;
    const disabled = await api<Trigger>(`${base}/triggers/${id}/disable`,
      { method: "POST" }, me.csrf_token);
    setTriggers((items) => items.map((item) => item.id === id ? disabled : item));
    announce("Trigger disabled.", "success");
  });

  const latestVersion = versions[0];
  const publishedTriggerKind = latestVersion?.graph.nodes.find((node) => node.type.startsWith("trigger."))?.type;
  const statusByNode = useMemo(() => new Map(run?.steps.map((step) => [step.node_id, step.status])), [run?.steps]);

  return <main className="studio-shell">
    <header className="studio-topbar">
      <a className="studio-brand" href="/"><span className="brand-mark" aria-hidden="true">A</span><span>automiq</span><span className="studio-brand-divider" /><small>STUDIO</small></a>
      <div className="studio-top-actions"><span className="studio-environment"><i /> LOCAL WORKSPACE</span>
        <button className="theme-button" type="button" onClick={switchTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? "☀ Light" : "◐ Dark"}</button>
        {me && <span className="user-pill" title={me.email}>{me.email.slice(0, 1).toUpperCase()}</span>}
      </div>
    </header>

    {authState === "loading" ? <section className="studio-centered" role="status"><div className="spinner" /><h1>Opening your workspace</h1><p>Checking your session and workflow access.</p></section> :
    authState === "signed_out" ? <section className="studio-centered auth-card">
      <div className="auth-symbol">↗</div><p className="studio-kicker">YOUR AUTOMATION SPACE</p>
      <h1>Bring your workflows to life.</h1><p>Sign in to build, publish, and follow every run from one place.</p>
      <a className="studio-primary" href="/auth/login">Continue with SSO <span aria-hidden="true">↗</span></a>
      {devIdentity && <div className="dev-signin"><span>LOCAL TEST IDENTITIES</span><div>
        {(["owner", "editor", "viewer"] as const).map((identity) =>
          <button key={identity} type="button" onClick={() => signIn(identity)} disabled={busy}>Sign in as {identity}</button>)}
      </div></div>}
    </section> : <div className="studio-layout">
      <aside className="studio-sidebar" aria-label="Workspace navigation">
        <div className="sidebar-section-label">WORKSPACE</div>
        {me?.workspaces.length ? <label className="workspace-select-label">Current workspace
          <select value={workspaceId || ""} onChange={(event) => { if (dirty && !window.confirm("Leave this unsaved draft?")) return; setWorkspaceId(event.target.value); }}>
            {me.workspaces.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></label> : <div className="sidebar-empty">No workspace yet. Create one below.</div>}
        <form className="sidebar-form" onSubmit={(event) => { event.preventDefault(); createWorkspace(); }}>
          <label htmlFor="workspace-name">New workspace</label><div className="inline-input"><input id="workspace-name" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="Team name" maxLength={120} /><button type="submit" disabled={busy || !workspaceName.trim()} aria-label="Create workspace">＋</button></div>
        </form>
        {workspace && <><div className="sidebar-section-label workflow-label">WORKFLOWS <span>{workflows.length}</span></div>
          {role !== "viewer" && <form className="sidebar-form" onSubmit={(event) => { event.preventDefault(); createWorkflow(); }}>
            <label htmlFor="workflow-name">Create a workflow</label><div className="inline-input"><input id="workflow-name" value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="e.g. Issue triage" maxLength={160} /><button type="submit" disabled={busy || !workflowName.trim()} aria-label="Create workflow">＋</button></div>
            <label htmlFor="workflow-template">Starting point</label><select id="workflow-template" value={workflowTemplate} onChange={(event) => setWorkflowTemplate(event.target.value as "blank" | "starter")}><option value="blank">Blank canvas</option><option value="starter">Manual → mock action → end</option></select>
          </form>}
          <nav className="workflow-list" aria-label="Workflows">{workflows.length ? workflows.map((item) => <div key={item.id} className={`workflow-list-item ${selected?.id === item.id ? "active" : ""}`}>
            <button type="button" onClick={() => { if (dirty && !window.confirm("Leave this unsaved draft?")) return; void openWorkflow(workspace.id, item.id); }}>
              <span className="workflow-list-icon">◇</span><span><strong>{item.name}</strong><small>{item.status === "archived" ? "Archived" : item.published_version_id ? "Published" : "Draft"}</small></span>
            </button>
            {role !== "viewer" && item.status === "active" && <div className="workflow-item-actions"><button type="button" aria-label={`Rename ${item.name}`} onClick={() => { setRenaming(item.id); setRenameText(item.name); }}>✎</button><button type="button" aria-label={`Archive ${item.name}`} onClick={() => setArchiveTarget(item.id)}>×</button></div>}
          </div>) : <div className="sidebar-empty">Your workflow list is empty.</div>}</nav>
          {renaming && <form className="sidebar-popover" onSubmit={(event) => { event.preventDefault(); rename(); }}><label htmlFor="rename-workflow">Rename workflow</label><input id="rename-workflow" value={renameText} onChange={(event) => setRenameText(event.target.value)} /><div><button type="button" onClick={() => setRenaming(null)}>Cancel</button><button type="submit" disabled={busy}>Save name</button></div></form>}
          {archiveTarget && <div className="sidebar-popover" role="dialog" aria-label="Archive workflow"><strong>Archive workflow?</strong><p>Its published versions and run history stay available to view.</p><div><button type="button" onClick={() => setArchiveTarget(null)}>Cancel</button><button type="button" onClick={archive} disabled={busy}>Archive</button></div></div>}
          {role === "owner" && <div className="member-panel"><div className="sidebar-section-label">MEMBERS <span>{members.length}</span></div><form onSubmit={(event) => { event.preventDefault(); grantMember(); }}><label htmlFor="member-email">Grant workspace access</label><input id="member-email" type="email" value={memberEmail} onChange={(event) => setMemberEmail(event.target.value)} placeholder="Signed-in email" required /><select aria-label="Member role" value={memberRole} onChange={(event) => setMemberRole(event.target.value as Role)}><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="owner">Owner</option></select><button type="submit" disabled={busy || !memberEmail.trim()}>Grant access</button></form></div>}
        </>}
        <div className="sidebar-bottom"><span className="role-badge">{role || "MEMBER"}</span><button type="button" onClick={() => void runAction(async () => { if (me) await api("/auth/logout", { method: "POST" }, me.csrf_token); setMe(null); setAuthState("signed_out"); setWorkspaceId(null); })}>Sign out</button></div>
      </aside>

      <section className="studio-main">
        {notice && <div className={`studio-notice ${notice.kind}`} role="status"><span>{notice.kind === "error" ? "!" : "✓"}</span>{notice.text}<button type="button" aria-label="Dismiss notice" onClick={() => setNotice(null)}>×</button></div>}
        {!selected || !graph || !workspace ? <div className="studio-empty"><span className="empty-symbol">◇</span><p className="studio-kicker">YOUR CANVAS AWAITS</p><h1>{workspace ? "Create your first workflow" : "Create a workspace"}</h1><p>{workspace ? "Name a workflow in the sidebar. Choose the starter to publish and run a working example, or begin with a blank canvas." : "Workspaces keep your workflows and runs organized with clear access roles."}</p></div> : <>
          <div className="studio-heading"><div><p className="studio-kicker">{workspace.name.toUpperCase()} / WORKFLOW BUILDER</p><h1>{selected.name}</h1><div className="heading-meta"><span className={`status-chip ${selected.status}`}>{selected.status}</span><span>Draft revision {selected.draft_revision}</span><span>{latestVersion ? `Published v${latestVersion.version}` : "Not published"}</span>{dirty && <span className="unsaved-dot">Unsaved changes</span>}</div></div>
            <div className="heading-actions"><button type="button" className="studio-secondary" onClick={validate} disabled={!canEdit || busy}>Validate</button><button type="button" className="studio-secondary" onClick={save} disabled={!canEdit || !dirty || busy}>Save draft</button><button type="button" className="studio-primary" onClick={() => setConfirmPublish(true)} disabled={!canEdit || busy || dirty}>Publish <span aria-hidden="true">↗</span></button></div>
          </div>
          <div className="studio-overview" aria-label="Workspace overview">
            <div><span>Workflows</span><strong>{workflows.filter((item) => item.status === "active").length}</strong><small>Active in this workspace</small></div>
            <div><span>Published</span><strong>{workflows.filter((item) => item.status === "active" && item.published_version_id).length}</strong><small>Ready for a run</small></div>
            <div><span>Recent runs</span><strong>{recentRuns.length}</strong><small>For this workflow</small></div>
          </div>
          {diagnostics.length > 0 && <div className="diagnostics" role="alert"><strong>Graph needs attention</strong><ul>{diagnostics.map((item, index) => <li key={`${item.code}-${index}`}><span>{item.node_id || "Graph"}</span>{item.message}</li>)}</ul></div>}
          {confirmPublish && <div className="publish-confirm" role="dialog" aria-label="Publish workflow"><div><strong>Publish this draft?</strong><p>A new immutable version will be available for manual runs. Current runs keep their pinned version.</p></div><div><button type="button" className="studio-secondary" onClick={() => setConfirmPublish(false)}>Cancel</button><button type="button" className="studio-primary" onClick={publish} disabled={busy}>Confirm publish</button></div></div>}
          <div className="editor-grid"><div className="editor-main">
            <div className="editor-toolbar"><div><span className="studio-kicker">CANVAS</span><strong>Shape the flow</strong></div><span>{graph.nodes.length} nodes · {graph.edges.length} connections</span></div>
            <div className="palette" aria-label="Node palette">{palette.map(({ kind, hint }) => <button key={kind} type="button" draggable={canEdit} onDragStart={(event: DragEvent<HTMLButtonElement>) => event.dataTransfer.setData("application/automiq-node", kind)} onClick={() => addNode(kind)} disabled={!canEdit} title={`Drag ${labels[kind]} to canvas or click to add`}><span className="palette-icon">＋</span><span><strong>{labels[kind]}</strong><small>{hint}</small></span></button>)}</div>
            <WorkflowCanvas graph={graph} onChange={changeGraph} onSelect={setSelectedNodeId} selectedId={selectedNodeId} readOnly={!canEdit} run={run} theme={theme} onNotice={(message) => announce(message, message.includes("cannot") || message.includes("only") || message.includes("Cycles") ? "error" : "info")} />
            <div className="connection-editor"><div className="connection-fields"><label htmlFor="link-source">From<select id="link-source" aria-label="From node" value={linkSource} onChange={(event) => setLinkSource(event.target.value)} disabled={!canEdit}><option value="">Choose node</option>{graph.nodes.filter((node) => node.type !== "end").map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label>
              {graph.nodes.find((node) => node.id === linkSource)?.type === "condition" && <label htmlFor="link-branch">Branch<select id="link-branch" aria-label="Branch" value={linkBranch} onChange={(event) => setLinkBranch(event.target.value as "true" | "false")} disabled={!canEdit}><option value="true">True</option><option value="false">False</option></select></label>}
              <label htmlFor="link-target">To<select id="link-target" aria-label="To node" value={linkTarget} onChange={(event) => setLinkTarget(event.target.value)} disabled={!canEdit}><option value="">Choose node</option>{graph.nodes.filter((node) => !node.type.startsWith("trigger.")).map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label>
              <button type="button" className="studio-secondary" onClick={addConnection} disabled={!canEdit || !linkSource || !linkTarget}>Connect nodes</button></div>
              <div className="connection-list">{graph.edges.length ? graph.edges.map((edge, index) => <span key={`${edge.source}-${edge.target}-${index}`}>{edge.source}{edge.source_handle && ` [${edge.source_handle}]`} → {edge.target}{canEdit && <button type="button" aria-label={`Remove connection ${edge.source} to ${edge.target}`} onClick={() => changeGraph({ ...graph, edges: graph.edges.filter((_, position) => position !== index) })}>×</button>}</span>) : <small>No connections yet.</small>}</div>
            </div>
            <p className="canvas-help">Drag from a node handle to connect it. Conditions need both true and false paths. Select a node to configure it.</p>
          </div><aside className="inspector" aria-label="Node inspector">
            <div className="inspector-title"><span className="studio-kicker">INSPECTOR</span><strong>{selectedNode ? labels[selectedNode.type] : "Select a node"}</strong></div>
            {selectedNode ? <div className="inspector-content"><div className="inspector-id"><span>NODE ID</span><code>{selectedNode.id}</code><span className={`step-state ${statusByNode.get(selectedNode.id) || "idle"}`}>{statusByNode.get(selectedNode.id) || "Not run"}</span></div>
              {selectedNode.type === "agent" && <>
                <label htmlFor="agent-profile">Model profile</label><select id="agent-profile" value={String(selectedNode.config.model_profile || "fake")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, model_profile: event.target.value } })} disabled={!canEdit}><option value="fake">Local fake</option><option value="openai">Configured OpenAI</option></select>
                <label htmlFor="agent-instructions">Instructions</label><textarea id="agent-instructions" value={String(selectedNode.config.instructions || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, instructions: event.target.value } })} disabled={!canEdit} maxLength={2000} rows={4} />
                <label htmlFor="agent-tool">Read-only tool</label><select id="agent-tool" value={Array.isArray(selectedNode.config.allowed_tools) && selectedNode.config.allowed_tools.length ? "github.search_issues" : "none"} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, allowed_tools: event.target.value === "none" ? [] : ["github.search_issues"], github_integration_id: event.target.value === "none" ? null : (selectedNode.config.github_integration_id || "00000000-0000-0000-0000-000000000000") } })} disabled={!canEdit}><option value="none">No tools</option><option value="github.search_issues">GitHub issue search</option></select>
                {Array.isArray(selectedNode.config.allowed_tools) && selectedNode.config.allowed_tools.length > 0 && <><label htmlFor="agent-github">GitHub integration</label><select id="agent-github" value={String(selectedNode.config.github_integration_id || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, github_integration_id: event.target.value } })} disabled={!canEdit}><option value="00000000-0000-0000-0000-000000000000">Choose an integration</option>{integrations.filter((item) => item.provider === "github" && !item.revoked_at).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></>}
                <label htmlFor="agent-tool-calls">Maximum tool calls</label><input id="agent-tool-calls" type="number" min="0" max="5" value={Number(selectedNode.config.max_tool_calls ?? 1)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, max_tool_calls: Number(event.target.value) } })} disabled={!canEdit} />
                <label htmlFor="agent-duration">Maximum duration (seconds)</label><input id="agent-duration" type="number" min="1" max="120" value={Number(selectedNode.config.max_duration_seconds ?? 30)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, max_duration_seconds: Number(event.target.value) } })} disabled={!canEdit} />
                <label htmlFor="agent-input-tokens">Input token budget</label><input id="agent-input-tokens" type="number" min="100" max="8000" value={Number(selectedNode.config.max_input_tokens ?? 2000)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, max_input_tokens: Number(event.target.value) } })} disabled={!canEdit} />
                <label htmlFor="agent-output-tokens">Output token budget</label><input id="agent-output-tokens" type="number" min="30" max="2000" value={Number(selectedNode.config.max_output_tokens ?? 300)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, max_output_tokens: Number(event.target.value) } })} disabled={!canEdit} />
                <label htmlFor="agent-cost">Cost budget (micro USD)</label><input id="agent-cost" type="number" min="0" max="1000000" value={Number(selectedNode.config.max_cost_microusd ?? 100000)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, max_cost_microusd: Number(event.target.value) } })} disabled={!canEdit} />
                <p>The output contract requires severity and reason. Untrusted issue text cannot grant a tool or approval.</p>
              </>}
              {selectedNode.type === "approval" && <><label htmlFor="approval-title">Approval request</label><input id="approval-title" value={String(selectedNode.config.title || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, title: event.target.value } })} disabled={!canEdit} maxLength={160} /><label htmlFor="approval-timeout">Timeout (seconds)</label><input id="approval-timeout" type="number" min="1" max="604800" value={Number(selectedNode.config.timeout_seconds ?? 3600)} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, timeout_seconds: Number(event.target.value) } })} disabled={!canEdit} /><p>Only a workspace owner can approve. The run fails closed when the time expires or the request is rejected.</p></>}
              {selectedNode.type === "action.http" && <>
                <label htmlFor="http-operation">Action mode</label>
                <select id="http-operation" value={String(selectedNode.config.operation || "mock")} onChange={(event) => updateNode({ ...selectedNode, config: event.target.value === "https_get" ? { operation: "https_get", path: "/status", response_fields: ["ok"], timeout_seconds: 10 } : { operation: "mock", mock_output: {}, timeout_seconds: 10 } })} disabled={!canEdit}>
                  <option value="mock">Local mock</option><option value="https_get">Configured HTTPS GET</option>
                </select>
                {selectedNode.config.operation === "https_get" ? <>
                  <label htmlFor="http-path">Request path</label><input id="http-path" value={String(selectedNode.config.path || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, path: event.target.value } })} disabled={!canEdit} placeholder="/status" />
                  <label htmlFor="http-fields">Numeric or boolean response fields</label><input id="http-fields" value={Array.isArray(selectedNode.config.response_fields) ? selectedNode.config.response_fields.join(", ") : ""} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, response_fields: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } })} disabled={!canEdit} placeholder="ok, count" />
                  <p>The server supplies the destination. Only selected scalar fields appear in run history. Publishing requires the connector to be enabled by the operator.</p>
                </> : <>
                  <label htmlFor="mock-output">Mock response JSON</label><textarea id="mock-output" value={configText} onChange={(event) => setConfigText(event.target.value)} onBlur={() => { try { const output = parsePayload(configText); updateNode({ ...selectedNode, config: { ...selectedNode.config, mock_output: output } }); } catch (error) { announce(errorText(error), "error"); } }} disabled={!canEdit} rows={6} /><p>Returned by the local mock adapter without an outbound call.</p>
                </>}
              </>}
              {(selectedNode.type === "action.github_comment" || selectedNode.type === "action.slack_message") && <>
                <label htmlFor="action-integration">Integration</label>
                <select id="action-integration" value={String(selectedNode.config.integration_id || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, integration_id: event.target.value } })} disabled={!canEdit}>
                  <option value="00000000-0000-0000-0000-000000000000">Choose an integration</option>
                  {integrations.filter((item) => item.provider === (selectedNode.type === "action.github_comment" ? "github" : "slack") && !item.revoked_at).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
                </select>
                {selectedNode.type === "action.github_comment" ? <><label htmlFor="github-comment-body">Comment</label><textarea id="github-comment-body" value={String(selectedNode.config.body || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, body: event.target.value } })} disabled={!canEdit} rows={4} maxLength={4000} /><p>Posts to the issue that started this run. An uncertain provider result stops for review.</p></> : <><label htmlFor="slack-channel">Channel</label><input id="slack-channel" value={String(selectedNode.config.channel || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, channel: event.target.value } })} disabled={!canEdit} /><label htmlFor="slack-text">Message</label><textarea id="slack-text" value={String(selectedNode.config.text || "")} onChange={(event) => updateNode({ ...selectedNode, config: { ...selectedNode.config, text: event.target.value } })} disabled={!canEdit} rows={4} maxLength={4000} /></>}
              </>}
              {selectedNode.type === "condition" && <><label htmlFor="condition-path">Value path</label><input id="condition-path" value={String((selectedNode.config.expression as Record<string, unknown>)?.path || "")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), path: event.target.value } } })} disabled={!canEdit} /><label htmlFor="condition-operator">Operator</label><select id="condition-operator" value={String((selectedNode.config.expression as Record<string, unknown>)?.operator || "eq")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), operator: event.target.value } } })} disabled={!canEdit}><option value="eq">Equals</option><option value="ne">Does not equal</option><option value="exists">Exists</option></select><label htmlFor="condition-value">Compare with</label><input id="condition-value" value={String((selectedNode.config.expression as Record<string, unknown>)?.value ?? "")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), value: event.target.value === "true" ? true : event.target.value === "false" ? false : event.target.value } } })} disabled={!canEdit} /><p>Use a path such as <code>trigger.payload.flag</code> or <code>steps.action_1.output.ok</code>.</p></>}
              {selectedNode.type === "trigger.github_issue" && <p>Signed GitHub issue events start this workflow. Create the webhook binding below after publishing.</p>}
              {selectedNode.type === "trigger.schedule" && <p>Temporal starts this workflow on a configured schedule. Add the cron binding below after publishing.</p>}
              {(selectedNode.type === "trigger.manual" || selectedNode.type === "end") && <p>{selectedNode.type === "end" ? "An end node completes this path. It cannot have outgoing connections." : "The manual trigger receives a JSON payload when you run the workflow."}</p>}
              {run?.steps.filter((step) => step.node_id === selectedNode.id).map((step) => <div className="inspector-attempt" key={step.attempt}><strong>Attempt {step.attempt} · {step.status}</strong>{step.output && <pre>{JSON.stringify(step.output, null, 2)}</pre>}{step.error && <p>{step.error}</p>}</div>)}
              {canEdit && !selectedNode.type.startsWith("trigger.") && <button type="button" className="danger-link" onClick={() => { changeGraph({ ...graph, nodes: graph.nodes.filter((node) => node.id !== selectedNode.id), edges: graph.edges.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id) }); setSelectedNodeId(null); }}>Remove node</button>}
            </div> : <div className="inspector-placeholder"><span>◇</span><p>Click a node to edit its settings and inspect its latest run result.</p></div>}
          </aside></div>

          <section className="integration-section" aria-labelledby="integration-title">
            <div className="run-section-header"><div><p className="studio-kicker">CONNECTIONS</p><h2 id="integration-title">Integrations & triggers</h2></div></div>
            <div className="integration-grid">
              <div className="integration-card"><h3>Workspace integrations</h3><p>Credentials are encrypted and never shown again after saving.</p>
                {integrations.length ? <ul className="connection-items">{integrations.map((item) => <li key={item.id}><div><strong>{item.display_name}</strong><small>{item.provider} · Credential {item.credential_version} · {item.revoked_at ? "Revoked" : "Active"}</small></div>{!item.revoked_at && item.can_configure && <div className="connection-actions"><button type="button" onClick={() => { setEditIntegrationId(item.id); setNewProvider(item.provider); setIntegrationName(item.display_name); setIntegrationToken(""); setWebhookSecret(""); setRepository(""); }}>Rotate</button>{role === "owner" && <><button type="button" onClick={() => { setAssignmentTarget(item.id); setAssignmentEditor(""); }} disabled={busy}>Assign</button><button type="button" onClick={() => revokeIntegration(item.id)} disabled={busy}>Revoke</button></>}</div>}</li>)}</ul> : <p className="connection-empty">No integrations connected.</p>}
                {role === "owner" && assignmentTarget && <form className="connection-form" onSubmit={(event) => { event.preventDefault(); assignIntegration(); }}><strong>Assign an editor</strong><label htmlFor="assignment-editor">Editor</label><select id="assignment-editor" value={assignmentEditor} onChange={(event) => setAssignmentEditor(event.target.value)} required><option value="">Choose editor</option>{members.filter((item) => item.role === "editor").map((item) => <option key={item.user_id} value={item.user_id}>{item.email}</option>)}</select><div className="connection-actions"><button type="submit" className="studio-primary" disabled={busy || !assignmentEditor}>Assign editor</button><button type="button" onClick={() => setAssignmentTarget("")}>Cancel</button></div></form>}
                {(role === "owner" || editIntegrationId) && <form className="connection-form" onSubmit={(event) => { event.preventDefault(); saveIntegration(); }}>
                  <strong>{editIntegrationId ? "Rotate credential" : "Add integration"}</strong>
                  <label htmlFor="integration-provider">Provider</label><select id="integration-provider" value={newProvider} onChange={(event) => setNewProvider(event.target.value as "github" | "slack")} disabled={busy || !!editIntegrationId}><option value="github">GitHub</option><option value="slack">Slack</option></select>
                  <label htmlFor="integration-name">Name</label><input id="integration-name" value={integrationName} onChange={(event) => setIntegrationName(event.target.value)} required maxLength={120} placeholder="Team integration" />
                  {newProvider === "github" && <><label htmlFor="integration-repository">Repository</label><input id="integration-repository" value={repository} onChange={(event) => setRepository(event.target.value)} required placeholder="owner/repository" /><label htmlFor="integration-secret">Webhook secret</label><input id="integration-secret" type="password" autoComplete="off" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} required minLength={16} /></>}
                  <label htmlFor="integration-token">{newProvider === "github" ? "GitHub token" : "Slack bot token"}</label><input id="integration-token" type="password" autoComplete="off" value={integrationToken} onChange={(event) => setIntegrationToken(event.target.value)} required minLength={8} />
                  <div className="connection-actions"><button className="studio-primary" type="submit" disabled={busy}>Save integration</button>{editIntegrationId && <button type="button" onClick={() => { setEditIntegrationId(""); setIntegrationName(""); setIntegrationToken(""); setWebhookSecret(""); setRepository(""); }}>Cancel</button>}</div>
                </form>}
              </div>
              <div className="integration-card"><h3>Published triggers</h3><p>Each binding stays pinned to the version shown when it was created.</p>
                {triggers.length ? <ul className="connection-items">{triggers.map((item) => <li key={item.id}><div><strong>{item.type === "github.issue" ? "GitHub issues" : "Schedule"}</strong><small>{item.enabled ? "Active" : "Disabled"} · Version {versions.find((version) => version.id === item.version_id)?.version || item.version_id.slice(0, 8)}</small>{item.webhook_url && <code className="webhook-path">{item.webhook_url}</code>}{item.type === "schedule" && <small>{item.config.cron} · {item.config.timezone}</small>}</div>{canEdit && item.enabled && <button type="button" onClick={() => disableTrigger(item.id)} disabled={busy}>Disable</button>}</li>)}</ul> : <p className="connection-empty">Publish a GitHub or schedule workflow, then bind its trigger here.</p>}
                {canEdit && selected.published_version_id && publishedTriggerKind === "trigger.github_issue" && <form className="connection-form" onSubmit={(event) => { event.preventDefault(); bindGithubTrigger(); }}><label htmlFor="trigger-integration">GitHub integration</label><select id="trigger-integration" value={triggerIntegration} onChange={(event) => setTriggerIntegration(event.target.value)} required><option value="">Choose integration</option>{integrations.filter((item) => item.provider === "github" && !item.revoked_at).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select><button className="studio-primary" type="submit" disabled={busy || !triggerIntegration}>Create webhook</button></form>}
                {canEdit && selected.published_version_id && publishedTriggerKind === "trigger.schedule" && <form className="connection-form" onSubmit={(event) => { event.preventDefault(); bindSchedule(); }}><label htmlFor="schedule-cron">Five-field cron</label><input id="schedule-cron" value={scheduleCron} onChange={(event) => setScheduleCron(event.target.value)} required placeholder="0 9 * * 1-5" /><label htmlFor="schedule-timezone">IANA timezone</label><input id="schedule-timezone" value={scheduleTimezone} onChange={(event) => setScheduleTimezone(event.target.value)} required placeholder="UTC" /><button className="studio-primary" type="submit" disabled={busy}>Create schedule</button></form>}
              </div>
            </div>
          </section>

          <section className="integration-section" aria-labelledby="approval-inbox-title">
            <div className="run-section-header"><div><p className="studio-kicker">HUMAN CHECKPOINTS</p><h2 id="approval-inbox-title">Approval inbox</h2></div><span>{approvals.filter((item) => item.status === "pending").length} pending</span></div>
            {approvals.length ? <ul className="connection-items approval-inbox">{approvals.map((item) => <li key={item.id}><div><strong>{item.title}</strong><small>Run {item.run_id.slice(0, 8)} · {item.node_id} · {item.status} · Due {formatTime(item.expires_at)}</small></div>{item.can_approve && <div className="connection-actions"><button type="button" className="studio-primary" onClick={() => decideApproval(item, "approved")} disabled={busy}>Approve</button><button type="button" className="studio-secondary" onClick={() => decideApproval(item, "rejected")} disabled={busy}>Reject</button></div>}</li>)}</ul> : <p className="connection-empty">No approval requests in this workspace.</p>}
          </section>
          <section className="run-section" aria-labelledby="run-title"><div className="run-section-header"><div><p className="studio-kicker">EXECUTION</p><h2 id="run-title">Run & inspect</h2></div><span className={`connection-pill ${connection}`}>{connection === "live" ? "● Live events" : connection === "polling" ? "○ Polling" : connection === "complete" ? "✓ Complete" : "No active stream"}</span></div>
            {selected.published_version_id && publishedTriggerKind === "trigger.manual" ? <div className="run-controls"><div><label htmlFor="run-input">Manual trigger payload</label><textarea id="run-input" value={runInput} onChange={(event) => setRunInput(event.target.value)} disabled={!canEdit} rows={2} /><small>JSON object with text, number, boolean, or null values.</small></div><button type="button" className="studio-primary" onClick={startRun} disabled={!canEdit || busy}>▶ Start run</button></div> : <div className="run-empty">{selected.published_version_id ? "Runs appear here when a signed webhook or schedule fires." : "Publish a valid draft to start a run."}</div>}
            {recentRuns.length > 0 && <div className="recent-runs"><label htmlFor="recent-run">Recent runs</label><select id="recent-run" value={runId || ""} onChange={(event) => { setRunId(event.target.value); setTimeline([]); }}><option value="">Choose a run</option>{recentRuns.map((item) => <option value={item.run_id} key={item.run_id}>{formatTime(item.created_at)} · {item.status} · {item.run_id.slice(0, 8)}</option>)}</select></div>}
            {run && <div className="run-detail"><div className="run-summary"><div><span className="studio-kicker">RUN {run.run_id.slice(0, 8).toUpperCase()}</span><h3>{run.status === "succeeded" ? "Run completed" : run.status === "failed" ? "Run failed" : run.status === "queued" ? "Waiting for worker" : "Run in progress"}</h3><p>Started {formatTime(run.started_at)} · Version {versions.find((item) => item.id === run.version_id)?.version || run.version_id.slice(0, 8)}</p></div><span className={`run-status ${run.status}`}>{run.status}</span></div>
              {run.error && <p className="run-error" role="alert">{run.error}</p>}
              <div className="run-steps">{run.steps.length ? run.steps.map((step) => <article key={`${step.node_id}-${step.attempt}`}><span className={`step-dot ${step.status}`} /><div><strong>{step.node_id}</strong><small>Attempt {step.attempt} · {step.status}</small></div><time>{formatTime(step.finished_at || step.started_at)}</time></article>) : <div className="run-empty">The run is queued. Steps will appear here.</div>}</div>
              <div className="timeline"><div className="studio-kicker">EVENT TIMELINE</div>{timeline.length ? <ol ref={timelineRef}>{timeline.map((event) => <li key={event.id}><span>#{event.id}</span><strong>{event.type.replace(".", " / ")}</strong>{typeof event.payload.node_id === "string" && <small>{event.payload.node_id}</small>}</li>)}</ol> : <p>Waiting for persisted run events.</p>}</div>
            </div>}
          </section>
        </>}
      </section>
    </div>}
  </main>;
}

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

const palette: { kind: NodeKind; hint: string }[] = [
  { kind: "trigger.manual", hint: "Start here" },
  { kind: "action.http", hint: "Mock action" },
  { kind: "condition", hint: "True / false" },
  { kind: "end", hint: "Finish a path" },
];

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
  const [members, setMembers] = useState<Member[]>([]);
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
      const [workflow, published, runs] = await Promise.all([
        api<Workflow>(pathFor(ws, id)),
        api<Version[]>(`${pathFor(ws, id)}/versions`),
        api<RunSummary[]>(`${pathFor(ws, id)}/runs`),
      ]);
      setSelected(workflow); setGraph(workflow.draft_graph); setDirty(false); setSelectedNodeId(null);
      setLinkSource(""); setLinkTarget("");
      setDiagnostics([]); setVersions(published); setRecentRuns(runs);
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

  const changeGraph = (next: Graph) => { setGraph(next); setDirty(true); setDiagnostics([]); };
  const updateNode = (node: GraphNode) => {
    if (!graph) return;
    changeGraph({ ...graph, nodes: graph.nodes.map((item) => item.id === node.id ? node : item) });
  };
  const addNode = (kind: NodeKind) => {
    if (!graph || !canEdit) return;
    if (kind === "trigger.manual" && graph.nodes.some((node) => node.type === kind)) {
      announce("A workflow has one manual trigger.", "error"); return;
    }
    const isFirstAction = kind === "action.http" && graph.nodes.length === 2 &&
      graph.nodes.some((node) => node.type === "trigger.manual") &&
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
    setWorkflows((items) => [created, ...items]); setWorkflowName("");
    await openWorkflow(workspaceId, created.id); announce("Draft created. Add or connect nodes to begin.", "success");
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

  const latestVersion = versions[0];
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
        {!selected || !graph || !workspace ? <div className="studio-empty"><span className="empty-symbol">◇</span><p className="studio-kicker">YOUR CANVAS AWAITS</p><h1>{workspace ? "Create your first workflow" : "Create a workspace"}</h1><p>{workspace ? "Name a workflow in the sidebar, then connect a trigger, actions, and an end node." : "Workspaces keep your workflows and runs organized with clear access roles."}</p></div> : <>
          <div className="studio-heading"><div><p className="studio-kicker">{workspace.name.toUpperCase()} / WORKFLOW BUILDER</p><h1>{selected.name}</h1><div className="heading-meta"><span className={`status-chip ${selected.status}`}>{selected.status}</span><span>Draft revision {selected.draft_revision}</span><span>{latestVersion ? `Published v${latestVersion.version}` : "Not published"}</span>{dirty && <span className="unsaved-dot">Unsaved changes</span>}</div></div>
            <div className="heading-actions"><button type="button" className="studio-secondary" onClick={validate} disabled={!canEdit || busy}>Validate</button><button type="button" className="studio-secondary" onClick={save} disabled={!canEdit || !dirty || busy}>Save draft</button><button type="button" className="studio-primary" onClick={() => setConfirmPublish(true)} disabled={!canEdit || busy || dirty}>Publish <span aria-hidden="true">↗</span></button></div>
          </div>
          {diagnostics.length > 0 && <div className="diagnostics" role="alert"><strong>Graph needs attention</strong><ul>{diagnostics.map((item, index) => <li key={`${item.code}-${index}`}><span>{item.node_id || "Graph"}</span>{item.message}</li>)}</ul></div>}
          {confirmPublish && <div className="publish-confirm" role="dialog" aria-label="Publish workflow"><div><strong>Publish this draft?</strong><p>A new immutable version will be available for manual runs. Current runs keep their pinned version.</p></div><div><button type="button" className="studio-secondary" onClick={() => setConfirmPublish(false)}>Cancel</button><button type="button" className="studio-primary" onClick={publish} disabled={busy}>Confirm publish</button></div></div>}
          <div className="editor-grid"><div className="editor-main">
            <div className="editor-toolbar"><div><span className="studio-kicker">CANVAS</span><strong>Shape the flow</strong></div><span>{graph.nodes.length} nodes · {graph.edges.length} connections</span></div>
            <div className="palette" aria-label="Node palette">{palette.map(({ kind, hint }) => <button key={kind} type="button" draggable={canEdit} onDragStart={(event: DragEvent<HTMLButtonElement>) => event.dataTransfer.setData("application/automiq-node", kind)} onClick={() => addNode(kind)} disabled={!canEdit} title={`Drag ${labels[kind]} to canvas or click to add`}><span className="palette-icon">＋</span><span><strong>{labels[kind]}</strong><small>{hint}</small></span></button>)}</div>
            <WorkflowCanvas graph={graph} onChange={changeGraph} onSelect={setSelectedNodeId} selectedId={selectedNodeId} readOnly={!canEdit} run={run} theme={theme} onNotice={(message) => announce(message, message.includes("cannot") || message.includes("only") || message.includes("Cycles") ? "error" : "info")} />
            <div className="connection-editor"><div className="connection-fields"><label htmlFor="link-source">From<select id="link-source" aria-label="From node" value={linkSource} onChange={(event) => setLinkSource(event.target.value)} disabled={!canEdit}><option value="">Choose node</option>{graph.nodes.filter((node) => node.type !== "end").map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label>
              {graph.nodes.find((node) => node.id === linkSource)?.type === "condition" && <label htmlFor="link-branch">Branch<select id="link-branch" aria-label="Branch" value={linkBranch} onChange={(event) => setLinkBranch(event.target.value as "true" | "false")} disabled={!canEdit}><option value="true">True</option><option value="false">False</option></select></label>}
              <label htmlFor="link-target">To<select id="link-target" aria-label="To node" value={linkTarget} onChange={(event) => setLinkTarget(event.target.value)} disabled={!canEdit}><option value="">Choose node</option>{graph.nodes.filter((node) => node.type !== "trigger.manual").map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label>
              <button type="button" className="studio-secondary" onClick={addConnection} disabled={!canEdit || !linkSource || !linkTarget}>Connect nodes</button></div>
              <div className="connection-list">{graph.edges.length ? graph.edges.map((edge, index) => <span key={`${edge.source}-${edge.target}-${index}`}>{edge.source}{edge.source_handle && ` [${edge.source_handle}]`} → {edge.target}{canEdit && <button type="button" aria-label={`Remove connection ${edge.source} to ${edge.target}`} onClick={() => changeGraph({ ...graph, edges: graph.edges.filter((_, position) => position !== index) })}>×</button>}</span>) : <small>No connections yet.</small>}</div>
            </div>
            <p className="canvas-help">Drag from a node handle to connect it. Conditions need both true and false paths. Select a node to configure it.</p>
          </div><aside className="inspector" aria-label="Node inspector">
            <div className="inspector-title"><span className="studio-kicker">INSPECTOR</span><strong>{selectedNode ? labels[selectedNode.type] : "Select a node"}</strong></div>
            {selectedNode ? <div className="inspector-content"><div className="inspector-id"><span>NODE ID</span><code>{selectedNode.id}</code><span className={`step-state ${statusByNode.get(selectedNode.id) || "idle"}`}>{statusByNode.get(selectedNode.id) || "Not run"}</span></div>
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
              {selectedNode.type === "condition" && <><label htmlFor="condition-path">Value path</label><input id="condition-path" value={String((selectedNode.config.expression as Record<string, unknown>)?.path || "")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), path: event.target.value } } })} disabled={!canEdit} /><label htmlFor="condition-operator">Operator</label><select id="condition-operator" value={String((selectedNode.config.expression as Record<string, unknown>)?.operator || "eq")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), operator: event.target.value } } })} disabled={!canEdit}><option value="eq">Equals</option><option value="ne">Does not equal</option><option value="exists">Exists</option></select><label htmlFor="condition-value">Compare with</label><input id="condition-value" value={String((selectedNode.config.expression as Record<string, unknown>)?.value ?? "")} onChange={(event) => updateNode({ ...selectedNode, config: { expression: { ...(selectedNode.config.expression as object), value: event.target.value === "true" ? true : event.target.value === "false" ? false : event.target.value } } })} disabled={!canEdit} /><p>Use a path such as <code>trigger.payload.flag</code> or <code>steps.action_1.output.ok</code>.</p></>}
              {(selectedNode.type === "trigger.manual" || selectedNode.type === "end") && <p>{selectedNode.type === "end" ? "An end node completes this path. It cannot have outgoing connections." : "The manual trigger receives a JSON payload when you run the workflow."}</p>}
              {run?.steps.filter((step) => step.node_id === selectedNode.id).map((step) => <div className="inspector-attempt" key={step.attempt}><strong>Attempt {step.attempt} · {step.status}</strong>{step.output && <pre>{JSON.stringify(step.output, null, 2)}</pre>}{step.error && <p>{step.error}</p>}</div>)}
              {canEdit && selectedNode.type !== "trigger.manual" && <button type="button" className="danger-link" onClick={() => { changeGraph({ ...graph, nodes: graph.nodes.filter((node) => node.id !== selectedNode.id), edges: graph.edges.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id) }); setSelectedNodeId(null); }}>Remove node</button>}
            </div> : <div className="inspector-placeholder"><span>◇</span><p>Click a node to edit its settings and inspect its latest run result.</p></div>}
          </aside></div>

          <section className="run-section" aria-labelledby="run-title"><div className="run-section-header"><div><p className="studio-kicker">EXECUTION</p><h2 id="run-title">Run & inspect</h2></div><span className={`connection-pill ${connection}`}>{connection === "live" ? "● Live events" : connection === "polling" ? "○ Polling" : connection === "complete" ? "✓ Complete" : "No active stream"}</span></div>
            {selected.published_version_id ? <div className="run-controls"><div><label htmlFor="run-input">Manual trigger payload</label><textarea id="run-input" value={runInput} onChange={(event) => setRunInput(event.target.value)} disabled={!canEdit} rows={2} /><small>JSON object with text, number, boolean, or null values.</small></div><button type="button" className="studio-primary" onClick={startRun} disabled={!canEdit || busy}>▶ Start run</button></div> : <div className="run-empty">Publish a valid draft to start a run.</div>}
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

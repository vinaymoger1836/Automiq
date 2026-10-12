"use client";

import { useEffect, useState } from "react";
import { api, type Role } from "@/lib/studio-api";

export type AppConnection = {
  id: string;
  display_name: string;
  callback_url: string | null;
  webhook_url: string;
  inbound_schemas: Record<string, { properties: Record<string, { type: string }> }>;
  outbound_schemas: Record<string, { properties: Record<string, { type: string }> }>;
  contract_version: number;
  credential_version: number;
  revoked_at: string | null;
};

type Created = AppConnection & { inbound_secret: string; outbound_secret: string };

type Props = {
  workspaceId: string;
  role: Role | null;
  csrf: string;
  connections: AppConnection[];
  onChange: (connections: AppConnection[]) => void;
};

const example = (field: string) => JSON.stringify({
  "com.example.event.v1": {
    type: "object",
    properties: { [field]: { type: "string" } },
    required: [field],
    additionalProperties: false,
  },
}, null, 2);

export function AppConnectionsPanel({ workspaceId, role, csrf, connections, onChange }: Props) {
  const [name, setName] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [inboundText, setInboundText] = useState(example("item_id"));
  const [outboundText, setOutboundText] = useState("{}");
  const [created, setCreated] = useState<Created | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { setCreated(null); setError(""); }, [workspaceId]);

  async function save() {
    setBusy(true); setError("");
    try {
      const inbound_schemas: unknown = JSON.parse(inboundText);
      const outbound_schemas: unknown = JSON.parse(outboundText);
      if (!inbound_schemas || typeof inbound_schemas !== "object" || Array.isArray(inbound_schemas) ||
          !outbound_schemas || typeof outbound_schemas !== "object" || Array.isArray(outbound_schemas)) {
        throw new Error("Event definitions must be JSON objects.");
      }
      const result = await api<Created>(`/api/v1/workspaces/${workspaceId}/app-connections`, {
        method: "POST",
        body: JSON.stringify({ display_name: name.trim(), callback_url: callbackUrl.trim() || null, inbound_schemas, outbound_schemas }),
      }, csrf);
      setCreated(result);
      onChange([...connections, result]);
      setName(""); setCallbackUrl("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create app connection.");
    } finally { setBusy(false); }
  }

  async function rotate(id: string) {
    setBusy(true); setError(""); setCreated(null);
    try {
      const result = await api<Created>(`/api/v1/workspaces/${workspaceId}/app-connections/${id}/rotate`, { method: "POST" }, csrf);
      setCreated(result);
      onChange(connections.map((item) => item.id === id ? result : item));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not rotate secrets."); }
    finally { setBusy(false); }
  }

  async function revoke(id: string) {
    setBusy(true); setError(""); setCreated(null);
    try {
      const result = await api<AppConnection>(`/api/v1/workspaces/${workspaceId}/app-connections/${id}/revoke`, { method: "POST" }, csrf);
      onChange(connections.map((item) => item.id === id ? result : item));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not revoke connection."); }
    finally { setBusy(false); }
  }

  return <div className="integration-card">
    <h3>Custom apps</h3>
    <p>Connect apps that send signed CloudEvents. Each connection has its own event types and callback endpoint.</p>
    {connections.length ? <ul className="connection-items">{connections.map((item) => <li key={item.id}>
      <div><strong>{item.display_name}</strong><small>{item.revoked_at ? "Revoked" : `Active · Secret version ${item.credential_version}`}</small>
        <small>Incoming: {Object.keys(item.inbound_schemas).join(", ") || "None"}</small>
        <small>Outgoing: {Object.keys(item.outbound_schemas).join(", ") || "None"}</small>
        <code className="webhook-path">{item.webhook_url}</code>
      </div>
      {role === "owner" && !item.revoked_at && <div className="connection-actions">
        <button type="button" onClick={() => void rotate(item.id)} disabled={busy}>Rotate</button>
        <button type="button" onClick={() => void revoke(item.id)} disabled={busy}>Revoke</button>
      </div>}
    </li>)}</ul> : <p className="connection-empty">No custom apps connected.</p>}
    {created && <div role="status" className="connection-form">
      <strong>Copy these secrets into {created.display_name} now</strong>
      <p>They will not be shown again. The app uses the inbound secret to sign events and the outbound secret to verify callbacks.</p>
      <label htmlFor="app-inbound-secret">Inbound signing secret</label>
      <input id="app-inbound-secret" value={created.inbound_secret} readOnly onFocus={(event) => event.target.select()} />
      <label htmlFor="app-outbound-secret">Callback verification secret</label>
      <input id="app-outbound-secret" value={created.outbound_secret} readOnly onFocus={(event) => event.target.select()} />
      <button type="button" onClick={() => setCreated(null)}>Done copying</button>
    </div>}
    {role === "owner" && <form className="connection-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <strong>Add custom app</strong>
      <label htmlFor="app-name">Name</label><input id="app-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} />
      <label htmlFor="app-callback">HTTPS callback URL (optional for inbound-only apps)</label>
      <input id="app-callback" type="url" value={callbackUrl} onChange={(event) => setCallbackUrl(event.target.value)} placeholder="https://tasks.example.com/automiq/events" />
      <label htmlFor="app-inbound-types">Incoming event types and schemas</label>
      <textarea id="app-inbound-types" value={inboundText} onChange={(event) => setInboundText(event.target.value)} rows={8} spellCheck={false} />
      <label htmlFor="app-outbound-types">Outgoing event types and schemas</label>
      <textarea id="app-outbound-types" value={outboundText} onChange={(event) => setOutboundText(event.target.value)} rows={6} spellCheck={false} />
      <p>Use CloudEvents 1.0 event types and a bounded JSON Schema object. The callback URL is checked before use.</p>
      <button type="submit" className="studio-primary" disabled={busy}>Create connection</button>
    </form>}
    {error && <p role="alert" className="run-error">{error}</p>}
  </div>;
}

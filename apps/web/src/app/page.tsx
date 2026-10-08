"use client";

import { useCallback, useEffect, useState } from "react";

type Health = { status: string; checks: Record<string, string> };

const services = [
  { id: "postgres", label: "PostgreSQL", detail: "Business data" },
  { id: "redis", label: "Redis", detail: "Auxiliary cache" },
  { id: "temporal", label: "Temporal", detail: "Durable orchestration" },
];

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      const data: unknown = await response.json();
      if (typeof data !== "object" || data === null || !("checks" in data) || !("status" in data)) {
        throw new Error("Invalid health response");
      }
      setHealth(data as Health);
    } catch {
      setHealth({ status: "degraded", checks: { api: "unavailable" } });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
    void refresh();
  }, [refresh]);

  function switchTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("automiq-theme", next);
    setTheme(next);
  }

  const overall = loading ? "Checking services" : health?.status === "ok" ? "All systems ready" : "Services need attention";

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark" aria-hidden="true">A</span><span>automiq</span></div>
        <button className="theme-button" onClick={switchTheme} type="button" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? "☀ Light" : "☾ Dark"}
        </button>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <div className="eyebrow"><span className="eyebrow-dot" /> WORKSPACE / LOCAL DEVELOPMENT</div>
        <h1 id="page-title">Build with confidence.<br /><span>Run with clarity.</span></h1>
        <p>Welcome to Automiq. Your local workflow engine is taking shape. Check the foundation below before building your first automation.</p>
        <div className="hero-actions">
          <a className="primary-link" href="#health">View system status <span aria-hidden="true">↗</span></a>
          <span className="phase-label">PHASE 00 <span aria-hidden="true">/</span> FOUNDATION</span>
        </div>
      </section>

      <section className="status-section" id="health" aria-labelledby="status-title">
        <div className="section-heading"><div><div className="section-kicker">ENVIRONMENT HEALTH</div><h2 id="status-title">System status</h2></div><button className="refresh-button" onClick={() => void refresh()} disabled={loading} type="button">↻ Refresh status</button></div>
        <div className={`summary ${health?.status === "ok" ? "ready" : "pending"}`} role="status" aria-live="polite"><span className="summary-icon" aria-hidden="true">{health?.status === "ok" ? "✓" : "!"}</span><div><strong>{overall}</strong><span>{loading ? "Connecting to your local stack…" : health?.status === "ok" ? "Your API and infrastructure are responding." : "Start the stack or inspect the service logs."}</span></div><span className="summary-time">LIVE CHECK</span></div>
        <div className="service-grid">
          {services.map((service, index) => {
            const state = loading ? "checking" : health?.checks?.[service.id] ?? "unavailable";
            return <article className="service-card" key={service.id}><div className="card-top"><span className="card-index">0{index + 1}</span><span className={`service-state ${state === "ok" ? "is-ok" : ""}`}>{state === "ok" ? "● Connected" : state === "checking" ? "○ Checking" : "○ Unavailable"}</span></div><div className="card-icon" aria-hidden="true">{["▤", "◈", "◇"][index]}</div><h3>{service.label}</h3><p>{service.detail}</p></article>;
          })}
        </div>
      </section>

      <footer><span>AN ENGINE FOR WHAT&apos;S NEXT.</span><span>LOCAL ENVIRONMENT · V0.1</span></footer>
    </main>
  );
}

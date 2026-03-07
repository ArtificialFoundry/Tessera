/** Servers management page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { api, type ServersResponse, type ServerInfo } from "@/lib/api";
import { toast, poll } from "@/lib/utils";
import { Shell, showConfirm } from "@/components/Shell";
import "@/styles/tessera.css";

const data = signal<ServersResponse | null>(null);
const busy = signal<string | null>(null);

const poller = poll(() => api.listServers(), data, 5_000);

function roleColor(role: string): string {
  if (role === "active") return "var(--green)";
  if (role === "candidate") return "var(--yellow)";
  return "var(--text-dim)";
}

function statusDot(status: string): string {
  if (status === "healthy" || status === "ok") return "online";
  if (status === "degraded") return "warn";
  return "offline";
}

async function promote(name: string) {
  busy.value = name;
  try {
    const r = await api.promoteServer(name);
    toast(r.message, "success");
    data.value = await api.listServers();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function demote(name: string) {
  busy.value = name;
  try {
    const r = await api.demoteServer(name);
    toast(r.message, "success");
    data.value = await api.listServers();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

function ServerCard({ s }: { s: ServerInfo }) {
  const isActive = s.role === "active";
  return (
    <div class={`card server-card${isActive ? " active-server" : ""}`}>
      {isActive && (
        <div class="active-badge serving">
          <span class="pulse-dot green" /> Active
        </div>
      )}
      <div class="server-header">
        <div class={`server-icon ${s.role}`}>🖥</div>
        <div>
          <div class="server-name">{s.name}</div>
          <div class="server-host">{s.url}</div>
        </div>
      </div>
      <span class="server-role" style={`color:${roleColor(s.role)}`}>{s.role}</span>
      <div class="server-health">
        <span class={`dot ${statusDot(s.status)}`} />
        <span>{s.status}</span>
      </div>
      {s.message && <div style="font-size:12px;color:var(--text-dim);margin-top:4px">{s.message}</div>}
      <div style="display:flex;gap:8px;margin-top:12px">
        {s.role !== "active" && (
          <button
            class="btn btn-sm btn-accent"
            disabled={busy.value === s.name}
            onClick={() => showConfirm("Promote Server", `Promote ${s.name} to active?`, "Promote", () => promote(s.name))}
          >
            Promote
          </button>
        )}
        {s.role === "active" && (
          <button
            class="btn btn-sm btn-danger"
            disabled={busy.value === s.name}
            onClick={() => showConfirm("Demote Server", `Demote ${s.name} to candidate?`, "Demote", () => demote(s.name))}
          >
            Demote
          </button>
        )}
      </div>
    </div>
  );
}

function ServersPage() {
  useEffect(() => { poller.start(); return () => poller.stop(); }, []);

  const d = data.value;
  if (!d) return <Shell activeTab="servers"><div class="empty">Loading…</div></Shell>;

  const active = d.servers.filter((s) => s.role === "active");
  const healthy = d.servers.filter((s) => s.status === "healthy" || s.status === "ok");

  return (
    <Shell activeTab="servers">
      <div class="metrics-bar fade-up fade-up-1">
        <div class="card metric">
          <div class="metric-value">{d.servers.length}</div>
          <div class="metric-label">Total Servers</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--green)">{active.length}</div>
          <div class="metric-label">Active</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--green)">{healthy.length}</div>
          <div class="metric-label">Healthy</div>
        </div>
      </div>

      <div class="section-title fade-up fade-up-2">🖥 Server Pool</div>
      {d.servers.length === 0 ? (
        <div class="card empty fade-up fade-up-3">No servers configured</div>
      ) : (
        <div class="voters-grid fade-up fade-up-3">
          {d.servers.map((s) => <ServerCard key={s.name} s={s} />)}
        </div>
      )}
    </Shell>
  );
}

const root = document.getElementById("app");
if (root) render(<ServersPage />, root);

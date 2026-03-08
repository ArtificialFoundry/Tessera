/** Servers management page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { api, isAuthCancelled, type ServersResponse, type ServerInfo, type AddServerRequest } from "@/lib/api";
import { toast, poll } from "@/lib/utils";
import { Shell, Modal, openModal, closeModal, showConfirm, StaleBanner } from "@/components/Shell";
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
  if (status === "running" || status === "healthy" || status === "ok") return "online";
  if (status === "degraded") return "warn";
  if (status === "registered" || status === "starting") return "warn";
  return "offline";
}

function statusLabel(status: string): string {
  if (status === "running") return "Healthy";
  if (status === "degraded") return "Degraded";
  if (status === "registered") return "Pending";
  if (status === "starting") return "Starting";
  if (status === "stopped") return "Stopped";
  if (status === "failed") return "Failed";
  return status;
}

async function promote(name: string) {
  busy.value = name;
  try {
    const r = await api.promoteServer(name);
    toast(r.message, "success");
    data.value = await api.listServers();
  } catch (e: unknown) {
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
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
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function removeServer(name: string) {
  busy.value = name;
  try {
    const r = await api.removeServer(name);
    toast(r.message, "success");
    data.value = await api.listServers();
  } catch (e: unknown) {
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

function AddServerForm() {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [role, setRole] = useState("candidate");
  const [priority, setPriority] = useState("10");
  const [token, setToken] = useState("");
  const [adding, setAdding] = useState(false);

  async function handleAdd() {
    if (!name.trim() || !url.trim()) { toast("Name and URL are required", "error"); return; }
    setAdding(true);
    try {
      const body: AddServerRequest = { name: name.trim(), url: url.trim(), role, priority: parseInt(priority) || 10 };
      if (token.trim()) body.token = token.trim();
      const r = await api.addServer(body);
      toast(r.message, "success");
      data.value = await api.listServers();
      closeModal();
    } catch (e: unknown) {
      if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
    } finally {
      setAdding(false);
    }
  }

  return (
    <Modal name="add-server" width="480px">
      <h3>Add DHCP Server</h3>
      <div class="form-field">
        <label class="form-label">Name</label>
        <input class="input" placeholder="e.g. u3" value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
      </div>
      <div class="form-field">
        <label class="form-label">URL</label>
        <input class="input" placeholder="https://192.168.1.3:53443" value={url} onInput={(e) => setUrl((e.target as HTMLInputElement).value)} />
      </div>
      <div style="display:flex;gap:12px">
        <div class="form-field" style="flex:1">
          <label class="form-label">Role</label>
          <select class="input" value={role} onChange={(e) => setRole((e.target as HTMLSelectElement).value)}>
            <option value="candidate">Candidate</option>
            <option value="active">Active</option>
            <option value="observer">Observer</option>
          </select>
        </div>
        <div class="form-field" style="flex:1">
          <label class="form-label">Priority</label>
          <input class="input" type="number" min="0" value={priority} onInput={(e) => setPriority((e.target as HTMLInputElement).value)} />
        </div>
      </div>
      <div class="form-field">
        <label class="form-label">API Token <span style="color:var(--text-dim);font-weight:normal">(optional, overrides global)</span></label>
        <input class="input" type="password" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore placeholder="Per-server Technitium token" value={token} onInput={(e) => setToken((e.target as HTMLInputElement).value)} />
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
        <button class="btn btn-ghost" onClick={closeModal}>Cancel</button>
        <button class="btn btn-accent" disabled={adding || !name.trim() || !url.trim()} onClick={handleAdd}>
          {adding ? "Adding…" : "Add Server"}
        </button>
      </div>
    </Modal>
  );
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
        <span>{statusLabel(s.status)}</span>
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
        {s.role !== "active" && (
          <button
            class="btn btn-sm btn-ghost"
            disabled={busy.value === s.name}
            onClick={() => showConfirm(
              "Remove Server",
              `Remove ${s.name} from the pool? This cannot be undone.`,
              "Remove",
              () => removeServer(s.name),
            )}
            title="Remove from pool"
          >
            Remove
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
      <StaleBanner consecutiveErrors={poller.consecutiveErrors} />
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

      <div class="section-title fade-up fade-up-2" style="display:flex;align-items:center;justify-content:space-between">
        <span>🖥 Server Pool</span>
        <button class="btn btn-sm btn-accent" onClick={() => openModal("add-server")}>+ Add Server</button>
      </div>
      {d.servers.length === 0 ? (
        <div class="card empty fade-up fade-up-3">No servers configured</div>
      ) : (
        <div class="voters-grid fade-up fade-up-3">
          {d.servers.map((s) => <ServerCard key={s.name} s={s} />)}
        </div>
      )}
      <AddServerForm />
    </Shell>
  );
}

const root = document.getElementById("app");
if (root) render(<ServersPage />, root);

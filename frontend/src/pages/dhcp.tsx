/** DHCP scope management page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { api, type ScopeListItem, type PaginationMeta } from "@/lib/api";
import { toast } from "@/lib/utils";
import { Shell, Modal, openModal, closeModal, showConfirm, Paginator } from "@/components/Shell";
import "@/styles/tessera.css";

const scopes = signal<ScopeListItem[]>([]);

async function loadScopes() {
  try {
    const d = await api.listScopes();
    scopes.value = d.scopes ?? [];
  } catch { toast("Failed to load scopes", "error"); }
}

function DhcpPage() {
  useEffect(() => { loadScopes(); }, []);

  const enabled = scopes.value.filter((s) => s.enabled).length;

  return (
    <Shell activeTab="dhcp">
      <div class="metrics-bar fade-up fade-up-1">
        <div class="card metric"><div class="metric-value" style="color:var(--accent)">{scopes.value.length}</div><div class="metric-label">Total Scopes</div></div>
        <div class="card metric"><div class="metric-value" style="color:var(--green)">{enabled}</div><div class="metric-label">Enabled</div></div>
        <div class="card metric"><div class="metric-value" style="color:var(--red)">{scopes.value.length - enabled}</div><div class="metric-label">Disabled</div></div>
      </div>

      <div class="section-title fade-up fade-up-2">
        📡 DHCP Scopes
        <div class="actions"><button class="btn btn-primary" onClick={() => openModal("create-scope")}>+ New Scope</button></div>
      </div>

      <div class="scopes-grid fade-up fade-up-3">
        {scopes.value.map((s) => <ScopeCard key={s.name} scope={s} />)}
      </div>
      {scopes.value.length === 0 && <div class="card empty fade-up fade-up-3">No scopes found</div>}

      <ScopeDetailModal />
      <CreateScopeModal />
    </Shell>
  );
}

// -- Scope Card --------------------------------------------------------------

function ScopeCard({ scope: s }: { scope: ScopeListItem }) {
  async function toggle() {
    try {
      if (s.enabled) await api.disableScope(s.name);
      else await api.enableScope(s.name);
      toast(`Scope ${s.enabled ? "disabled" : "enabled"}`, "success");
      await loadScopes();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  function confirmDelete() {
    showConfirm("Delete Scope", `Permanently delete scope "${s.name}"?`, "Delete", async () => {
      try {
        await api.deleteScope(s.name);
        toast("Scope deleted", "success");
        closeModal();
        await loadScopes();
      } catch (e) { toast((e as Error).message, "error"); }
    });
  }

  return (
    <div class="scope-card-wrap">
      <div class="card scope-card" onClick={() => { selectedScope.value = s.name; openModal("scope"); }}>
        <div class="scope-name">{s.name}</div>
        <div class="scope-range">{s.start_address} — {s.end_address}</div>
        <div class="scope-range">{s.subnet_mask}</div>
        <span class={`scope-badge ${s.enabled ? "enabled" : "disabled"}`}>{s.enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <div class="scope-actions" onClick={(e) => e.stopPropagation()}>
        <button class={`btn btn-toggle${!s.enabled ? " enable-btn" : ""}`} onClick={toggle}>
          {s.enabled ? "⏸ Disable" : "▶ Enable"}
        </button>
        <button class="btn btn-delete" onClick={confirmDelete}>🗑</button>
      </div>
    </div>
  );
}

// -- Scope Detail Modal ------------------------------------------------------

const selectedScope = signal("");

function ScopeDetailModal() {
  const [tab, setTab] = useState("overview");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [leases, setLeases] = useState<Record<string, unknown>[]>([]);
  const [leasesPag, setLeasesPag] = useState<PaginationMeta>({ total: 0, offset: 0, limit: 50 });
  const [saving, setSaving] = useState(false);
  const [newRes, setNewRes] = useState({ mac: "", ip: "", host: "", comments: "" });
  const [editingMac, setEditingMac] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ ip: "", host: "", comments: "" });

  const name = selectedScope.value;

  useEffect(() => {
    if (!name) return;
    setTab("overview");
    refresh();
  }, [name]);

  async function refresh(leaseOffset = 0) {
    if (!name) return;
    try {
      const [dr, lr] = await Promise.all([api.getScope(name), api.listLeases(name, leaseOffset, 50)]);
      setDetail(dr.data as Record<string, unknown>);
      setLeases(lr.leases ?? []);
      setLeasesPag(lr.pagination);
    } catch { /* ignore */ }
  }

  if (!detail) return null;

  const reservations = (detail.reservedLeases ?? []) as Record<string, unknown>[];

  // Extract display data
  const d = detail;
  const networkConfig: Record<string, string> = {};
  if (d.startingAddress) networkConfig["Start Address"] = String(d.startingAddress);
  if (d.endingAddress) networkConfig["End Address"] = String(d.endingAddress);
  if (d.subnetMask) networkConfig["Subnet Mask"] = String(d.subnetMask);
  if (d.routerAddress) networkConfig["Gateway"] = String(d.routerAddress);
  if (d.domainName) networkConfig["Domain"] = String(d.domainName);
  if (d.dnsServers) networkConfig["DNS Servers"] = Array.isArray(d.dnsServers) ? d.dnsServers.join(", ") : String(d.dnsServers);

  async function saveSettings(formData: Record<string, unknown>) {
    setSaving(true);
    try {
      await api.updateScope(name, formData);
      toast("Scope settings saved", "success");
      await refresh();
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  }

  async function addReservation() {
    if (!newRes.mac || !newRes.ip) return;
    try {
      await api.addReservation(name, { hardware_address: newRes.mac, address: newRes.ip, host_name: newRes.host, comments: newRes.comments });
      toast(`Reservation added: ${newRes.ip}`, "success");
      setNewRes({ mac: "", ip: "", host: "", comments: "" });
      await refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  function startEdit(r: Record<string, unknown>) {
    setEditingMac(String(r.hardwareAddress));
    setEditForm({ ip: String(r.address || ""), host: String(r.hostName || ""), comments: String(r.comments || "") });
  }

  function cancelEdit() { setEditingMac(null); }

  async function saveEdit() {
    if (!editingMac) return;
    try {
      await api.updateReservation(name, editingMac, { address: editForm.ip, host_name: editForm.host, comments: editForm.comments });
      toast("Reservation updated", "success");
      setEditingMac(null);
      await refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  function confirmDeleteRes(r: Record<string, unknown>) {
    showConfirm("Delete Reservation", `Remove reservation for ${r.hostName || r.hardwareAddress}?`, "Delete", async () => {
      try {
        await api.deleteReservation(name, String(r.hardwareAddress));
        toast("Removed", "success");
        closeModal();
        setTimeout(() => { openModal("scope"); refresh(); }, 100);
      } catch (e) { toast((e as Error).message, "error"); }
    });
  }

  function confirmRemoveLease(l: Record<string, unknown>) {
    showConfirm("Remove Lease", `Remove lease for ${l.address}?`, "Remove", async () => {
      try {
        await api.removeLease(name, String(l.address));
        toast("Lease removed", "success");
        closeModal();
        setTimeout(() => { openModal("scope"); refresh(); }, 100);
      } catch (e) { toast((e as Error).message, "error"); }
    });
  }

  async function convertLease(l: Record<string, unknown>) {
    try {
      await api.convertLease(name, String(l.address));
      toast(`Converted ${l.address}`, "success");
      await refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  return (
    <Modal name="scope">
      <h2>{name}</h2>
      <div class="modal-tabs">
        {["overview", "settings", "reservations", "leases"].map((t) => (
          <button key={t} class={`modal-tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
            {t === "reservations" ? `Reservations (${reservations.length})` : t === "leases" ? `Leases (${leases.length})` : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <h3>Network Configuration</h3>
          <div class="card" style="margin-bottom:14px">
            <table>{Object.entries(networkConfig).map(([k, v]) => (
              <tr key={k}><td style="color:var(--text-dim);width:40%">{k}</td><td class="mono">{v}</td></tr>
            ))}</table>
          </div>
        </>
      )}

      {tab === "settings" && <SettingsTab detail={d} onSave={saveSettings} saving={saving} />}

      {tab === "reservations" && (
        <>
          <div class="card" style="overflow-x:auto;margin-bottom:14px">
            <table>
              <thead><tr><th>Host</th><th>IP</th><th>MAC</th><th>Notes</th><th></th></tr></thead>
              <tbody>
                {reservations.map((r) => {
                  const mac = String(r.hardwareAddress);
                  if (editingMac === mac) return (
                    <tr key={mac}>
                      <td><input class="inline-edit" value={editForm.host} onInput={(e) => setEditForm({ ...editForm, host: (e.target as HTMLInputElement).value })} /></td>
                      <td><input class="inline-edit mono" value={editForm.ip} onInput={(e) => setEditForm({ ...editForm, ip: (e.target as HTMLInputElement).value })} /></td>
                      <td class="mono">{mac}</td>
                      <td><input class="inline-edit" value={editForm.comments} onInput={(e) => setEditForm({ ...editForm, comments: (e.target as HTMLInputElement).value })} onKeyDown={(e) => e.key === "Enter" && saveEdit()} /></td>
                      <td style="white-space:nowrap">
                        <button class="btn btn-sm btn-success" onClick={saveEdit} title="Save">✓</button>
                        <button class="btn btn-sm btn-ghost" onClick={cancelEdit} title="Cancel">✕</button>
                      </td>
                    </tr>
                  );
                  return (
                    <tr key={mac}>
                      <td>{String(r.hostName || "—")}</td>
                      <td class="mono">{String(r.address)}</td>
                      <td class="mono">{mac}</td>
                      <td>{String(r.comments || "—")}</td>
                      <td style="white-space:nowrap">
                        <button class="btn btn-sm btn-ghost" onClick={() => startEdit(r)} title="Edit">✎</button>
                        <button class="btn btn-danger btn-sm" onClick={() => confirmDeleteRes(r)}>✕</button>
                      </td>
                    </tr>
                  );
                })}
                {reservations.length === 0 && <tr><td colSpan={5} style="text-align:center;color:var(--text-dim)">No reservations</td></tr>}
              </tbody>
            </table>
          </div>
          <h3>Add Reservation</h3>
          <div class="card">
            <div class="form-row">
              <div class="form-field"><label>MAC</label><input value={newRes.mac} onInput={(e) => setNewRes({ ...newRes, mac: (e.target as HTMLInputElement).value })} placeholder="AA:BB:CC:DD:EE:FF" /></div>
              <div class="form-field"><label>IP</label><input value={newRes.ip} onInput={(e) => setNewRes({ ...newRes, ip: (e.target as HTMLInputElement).value })} placeholder="10.0.0.x" /></div>
              <div class="form-field"><label>Host</label><input value={newRes.host} onInput={(e) => setNewRes({ ...newRes, host: (e.target as HTMLInputElement).value })} placeholder="hostname" /></div>
              <div class="form-field"><label>Notes</label><input value={newRes.comments} onInput={(e) => setNewRes({ ...newRes, comments: (e.target as HTMLInputElement).value })} placeholder="optional" onKeyDown={(e) => e.key === "Enter" && addReservation()} /></div>
            </div>
            <button class="btn btn-primary" onClick={addReservation} disabled={!newRes.mac || !newRes.ip}>Add Reservation</button>
          </div>
        </>
      )}

      {tab === "leases" && (
        <div class="card" style="overflow-x:auto">
          <table>
            <thead><tr><th>Address</th><th>MAC</th><th>Host</th><th>Type</th><th>Expires</th><th></th></tr></thead>
            <tbody>
              {leases.map((l) => (
                <tr key={String(l.address)}>
                  <td class="mono">{String(l.address)}</td>
                  <td class="mono">{String(l.hardwareAddress || "—")}</td>
                  <td>{String(l.hostName || "—")}</td>
                  <td><span class={`lease-type ${String(l.type || "").toLowerCase()}`}>{String(l.type || "Unknown")}</span></td>
                  <td class="mono">{String(l.expires || "—")}</td>
                  <td>
                    {String(l.type || "").toLowerCase() === "dynamic" && (
                      <button class="btn btn-sm btn-success" onClick={() => convertLease(l)} title="Convert to reservation">⇄</button>
                    )}
                    <button class="btn btn-sm btn-danger" onClick={() => confirmRemoveLease(l)} title="Remove lease">✕</button>
                  </td>
                </tr>
              ))}
              {leases.length === 0 && <tr><td colSpan={6} style="text-align:center;color:var(--text-dim)">No active leases</td></tr>}
            </tbody>
          </table>
          <Paginator total={leasesPag.total} offset={leasesPag.offset} limit={leasesPag.limit}
            onPage={(o) => refresh(o)}
          />
        </div>
      )}
    </Modal>
  );
}

// -- Settings Tab ------------------------------------------------------------

interface SettingsTabProps {
  detail: Record<string, unknown>;
  onSave: (data: Record<string, unknown>) => void;
  saving: boolean;
}

function SettingsTab({ detail: d, onSave, saving }: SettingsTabProps) {
  const [form, setForm] = useState(() => ({
    startingAddress: String(d.startingAddress ?? ""),
    endingAddress: String(d.endingAddress ?? ""),
    subnetMask: String(d.subnetMask ?? ""),
    routerAddress: String(d.routerAddress ?? ""),
    domainName: String(d.domainName ?? ""),
    dnsServers: Array.isArray(d.dnsServers) ? d.dnsServers.join(", ") : String(d.dnsServers ?? ""),
    leaseTimeDays: String(d.leaseTimeDays ?? 0),
    leaseTimeHours: String(d.leaseTimeHours ?? 0),
    leaseTimeMinutes: String(d.leaseTimeMinutes ?? 0),
    pingCheckEnabled: String(d.pingCheckEnabled ?? true),
    allowOnlyReservedLeases: String(d.allowOnlyReservedLeases ?? false),
    dnsUpdates: String(d.dnsUpdates ?? true),
    ignoreClientIdentifierOption: String(d.ignoreClientIdentifierOption ?? false),
    blockLocallyAdministeredMacAddresses: String(d.blockLocallyAdministeredMacAddresses ?? false),
    bootFileName: String(d.bootFileName ?? ""),
    serverHostName: String(d.serverHostName ?? ""),
    tftpServerAddresses: Array.isArray(d.tftpServerAddresses) ? d.tftpServerAddresses.join(", ") : "",
    offerDelayTime: String(d.offerDelayTime ?? 0),
  }));

  function set(key: string, val: string) { setForm((f) => ({ ...f, [key]: val })); }
  function toggleBool(key: string) { setForm((f) => ({ ...f, [key]: f[key as keyof typeof f] === "true" ? "false" : "true" })); }
  function boolOn(key: string): boolean { return form[key as keyof typeof form] === "true"; }

  return (
    <>
      <h3>Edit Scope Settings</h3>
      <div class="card" style="margin-bottom:14px">
        <div class="form-row">
          <div class="form-field"><label>Start Address</label><input value={form.startingAddress} onInput={(e) => set("startingAddress", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>End Address</label><input value={form.endingAddress} onInput={(e) => set("endingAddress", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <div class="form-row">
          <div class="form-field"><label>Subnet Mask</label><input value={form.subnetMask} onInput={(e) => set("subnetMask", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>Router / Gateway</label><input value={form.routerAddress} onInput={(e) => set("routerAddress", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <div class="form-row">
          <div class="form-field"><label>Domain Name</label><input value={form.domainName} onInput={(e) => set("domainName", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>DNS Servers (comma-sep)</label><input value={form.dnsServers} onInput={(e) => set("dnsServers", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <h3>Lease Time</h3>
        <div class="form-row">
          <div class="form-field"><label>Days</label><input type="number" min="0" value={form.leaseTimeDays} onInput={(e) => set("leaseTimeDays", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>Hours</label><input type="number" min="0" max="23" value={form.leaseTimeHours} onInput={(e) => set("leaseTimeHours", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>Minutes</label><input type="number" min="0" max="59" value={form.leaseTimeMinutes} onInput={(e) => set("leaseTimeMinutes", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <h3>Options</h3>
        <div class="form-row">
          <div class="form-field"><div class="toggle" onClick={() => toggleBool("pingCheckEnabled")}><div class={`toggle-track${boolOn("pingCheckEnabled") ? " on" : ""}`}><div class="toggle-knob" /></div><span>Ping Check</span></div></div>
          <div class="form-field"><div class="toggle" onClick={() => toggleBool("allowOnlyReservedLeases")}><div class={`toggle-track${boolOn("allowOnlyReservedLeases") ? " on" : ""}`}><div class="toggle-knob" /></div><span>Reserved Only</span></div></div>
          <div class="form-field"><div class="toggle" onClick={() => toggleBool("dnsUpdates")}><div class={`toggle-track${boolOn("dnsUpdates") ? " on" : ""}`}><div class="toggle-knob" /></div><span>DNS Updates</span></div></div>
        </div>
        <div class="form-row">
          <div class="form-field"><div class="toggle" onClick={() => toggleBool("ignoreClientIdentifierOption")}><div class={`toggle-track${boolOn("ignoreClientIdentifierOption") ? " on" : ""}`}><div class="toggle-knob" /></div><span>Ignore Client ID</span></div></div>
          <div class="form-field"><div class="toggle" onClick={() => toggleBool("blockLocallyAdministeredMacAddresses")}><div class={`toggle-track${boolOn("blockLocallyAdministeredMacAddresses") ? " on" : ""}`}><div class="toggle-knob" /></div><span>Block Random MACs</span></div></div>
        </div>
        <h3>PXE / Boot</h3>
        <div class="form-row">
          <div class="form-field"><label>Boot Filename</label><input value={form.bootFileName} onInput={(e) => set("bootFileName", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>Server Hostname</label><input value={form.serverHostName} onInput={(e) => set("serverHostName", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <div class="form-row">
          <div class="form-field"><label>TFTP Servers</label><input value={form.tftpServerAddresses} onInput={(e) => set("tftpServerAddresses", (e.target as HTMLInputElement).value)} /></div>
          <div class="form-field"><label>Offer Delay (ms)</label><input type="number" min="0" value={form.offerDelayTime} onInput={(e) => set("offerDelayTime", (e.target as HTMLInputElement).value)} /></div>
        </div>
        <div style="margin-top:14px;display:flex;gap:12px">
          <button class="btn btn-primary" onClick={() => onSave(form)} disabled={saving}>{saving ? "Saving…" : "Save Changes"}</button>
        </div>
      </div>
    </>
  );
}

// -- Create Scope Modal ------------------------------------------------------

function CreateScopeModal() {
  const [form, setForm] = useState({
    name: "", starting_address: "", ending_address: "", subnet_mask: "255.255.0.0",
    router_address: "", domain_name: "", dns_servers_str: "",
    lease_time_days: 1, lease_time_hours: 0, lease_time_minutes: 0,
  });

  function set(key: string, val: string | number) { setForm((f) => ({ ...f, [key]: val })); }

  async function create() {
    try {
      await api.createScope({
        ...form,
        dns_servers: form.dns_servers_str ? form.dns_servers_str.split(",").map((s) => s.trim()).filter(Boolean) : [],
      });
      toast(`Scope '${form.name}' created`, "success");
      closeModal();
      await loadScopes();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  return (
    <Modal name="create-scope" width="600px">
      <h2>Create DHCP Scope</h2>
      <div class="form-row"><div class="form-field"><label>Scope Name</label><input value={form.name} onInput={(e) => set("name", (e.target as HTMLInputElement).value)} placeholder="My Scope" /></div></div>
      <div class="form-row">
        <div class="form-field"><label>Start Address</label><input value={form.starting_address} onInput={(e) => set("starting_address", (e.target as HTMLInputElement).value)} placeholder="10.0.0.100" /></div>
        <div class="form-field"><label>End Address</label><input value={form.ending_address} onInput={(e) => set("ending_address", (e.target as HTMLInputElement).value)} placeholder="10.0.0.254" /></div>
      </div>
      <div class="form-row">
        <div class="form-field"><label>Subnet Mask</label><input value={form.subnet_mask} onInput={(e) => set("subnet_mask", (e.target as HTMLInputElement).value)} /></div>
        <div class="form-field"><label>Gateway</label><input value={form.router_address} onInput={(e) => set("router_address", (e.target as HTMLInputElement).value)} /></div>
      </div>
      <div class="form-row">
        <div class="form-field"><label>Domain</label><input value={form.domain_name} onInput={(e) => set("domain_name", (e.target as HTMLInputElement).value)} /></div>
        <div class="form-field"><label>DNS Servers</label><input value={form.dns_servers_str} onInput={(e) => set("dns_servers_str", (e.target as HTMLInputElement).value)} /></div>
      </div>
      <div style="margin-top:18px;display:flex;gap:12px;justify-content:flex-end">
        <button class="btn btn-ghost" onClick={closeModal}>Cancel</button>
        <button class="btn btn-primary" onClick={create} disabled={!form.name || !form.starting_address || !form.ending_address || !form.subnet_mask}>Create Scope</button>
      </div>
    </Modal>
  );
}

// -- Mount -------------------------------------------------------------------

const root = document.getElementById("app");
if (root) render(<DhcpPage />, root);

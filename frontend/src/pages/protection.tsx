/** Protection page: backup management + drift enforcement. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { api, isAuthCancelled, type BackupManifest, type BackupSettings, type EnforcementStatus, type DriftCheckResponse, type DriftEvent, type DriftChange, type PaginationMeta } from "@/lib/api";
import { toast, formatTime } from "@/lib/utils";
import { Shell, Modal, openModal, closeModal, showConfirm, Paginator } from "@/components/Shell";
import "@/styles/tessera.css";

const backups = signal<BackupManifest[]>([]);
const backupsPagination = signal<PaginationMeta>({ total: 0, offset: 0, limit: 20 });
const enforcement = signal<EnforcementStatus>({
  mode: "off", pinned_backup_id: "", check_interval: 300,
  last_check: 0, last_drift: 0, drift_count: 0, restore_count: 0,
  backup_on_pin: true, auto_restore_cooldown: 60, max_history: 50, history: [],
  history_pagination: { total: 0, offset: 0, limit: 20 },
});
const historyOffset = signal(0);
const backupSettings = signal<BackupSettings>({
  auto_enabled: false, cron_schedule: "", max_backups: 50,
  stored_backups: 0, next_run: 0, backup_dir: "",
});

async function loadBackups() {
  try {
    const r = await api.listBackups(backupsPagination.value.offset, 20);
    backups.value = r.backups ?? [];
    backupsPagination.value = r.pagination;
  } catch { /* */ }
}
async function loadEnforcement() {
  try { enforcement.value = await api.getEnforcement(historyOffset.value, 20); } catch { /* */ }
}
async function loadBackupSettings() {
  try { backupSettings.value = await api.getBackupSettings(); } catch { /* */ }
}
async function loadAll() {
  await Promise.all([loadBackups(), loadEnforcement(), loadBackupSettings()]);
}

// -- Page Component ----------------------------------------------------------

function ProtectionPage() {
  useEffect(() => { loadAll(); }, []);

  const enf = enforcement.value;

  return (
    <Shell activeTab="protection">
      {/* Metrics */}
      <div class="metrics-bar fade-up fade-up-1">
        <div class="card metric">
          <div class="metric-value" style={`color:${enf.mode === "enforce" ? "var(--green)" : enf.mode === "monitor" ? "var(--yellow)" : "var(--text-dim)"}`}>
            {(enf.mode ?? "off").toUpperCase()}
          </div>
          <div class="metric-label">Enforcement Mode</div>
        </div>
        <div class="card metric"><div class="metric-value" style="color:var(--accent)">{backupsPagination.value.total}</div><div class="metric-label">Backups</div></div>
        <div class="card metric"><div class="metric-value" style="color:#f97316">{enf.drift_count}</div><div class="metric-label">Drift Events</div></div>
        <div class="card metric"><div class="metric-value" style="color:var(--green)">{enf.restore_count}</div><div class="metric-label">Auto-Restores</div></div>
      </div>

      <EnforcementControls />
      <SettingsSection />
      <DriftHistory />
      <BackupsTable />

      <RestoreModal />
      <DriftDetailModal />
    </Shell>
  );
}

// -- Enforcement Controls ----------------------------------------------------

function EnforcementControls() {
  const [driftChecking, setDriftChecking] = useState(false);
  const [driftResult, setDriftResult] = useState<DriftCheckResponse | null>(null);
  const [driftTab, setDriftTab] = useState<"what_changed" | "restore_plan">("what_changed");
  const enf = enforcement.value;

  async function setMode(mode: string) {
    try { await api.setEnforcementMode(mode); toast(`Mode: ${mode}`, "success"); await loadEnforcement(); }
    catch (e) { toast((e as Error).message, "error"); }
  }

  async function unpin() {
    try { await api.unpinBackup(); toast("Unpinned", "success"); setDriftResult(null); await loadEnforcement(); }
    catch (e) { toast((e as Error).message, "error"); }
  }

  async function check() {
    setDriftChecking(true); setDriftResult(null);
    try { setDriftResult(await api.checkDrift()); setDriftTab("what_changed"); await loadEnforcement(); }
    catch (e) { toast((e as Error).message, "error"); }
    setDriftChecking(false);
  }

  async function restoreFromPinned() {
    if (!enf.pinned_backup_id) return;
    try {
      const d = await api.restoreBackup(enf.pinned_backup_id, false);
      toast(`Restored: ${d.total_changes} change(s) applied`, "success");
      setDriftResult(null); await loadEnforcement();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
  }

  async function acceptDrift() {
    try {
      const d = await api.acceptDrift();
      toast(d.message, "success");
      setDriftResult(null); await loadEnforcement(); await loadBackups();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
  }

  return (
    <>
      <div class="section-title fade-up fade-up-2">🛡️ State Enforcement</div>
      <div class="card fade-up fade-up-2" style="margin-bottom:24px;padding:20px">
        <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
          <div style="flex:1;min-width:200px">
            <div style="font-size:13px;color:var(--text-dim);margin-bottom:4px">Pinned Backup</div>
            <div style="font-family:var(--mono);font-size:14px">{enf.pinned_backup_id || "None"}</div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class={`btn btn-sm${enf.mode === "off" ? " btn-primary" : ""}`} onClick={() => setMode("off")} disabled={enf.mode === "off"}>Off</button>
            <button class={`btn btn-sm${enf.mode === "monitor" ? " btn-warning" : ""}`} onClick={() => setMode("monitor")} disabled={!enf.pinned_backup_id}>Monitor</button>
            <button class={`btn btn-sm${enf.mode === "enforce" ? " btn-success" : ""}`} onClick={() => setMode("enforce")} disabled={!enf.pinned_backup_id}>Enforce</button>
            <button class="btn btn-sm" onClick={check} disabled={!enf.pinned_backup_id || driftChecking}>{driftChecking ? "Checking…" : "🔍 Check Now"}</button>
            <button class="btn btn-sm btn-ghost" onClick={unpin} disabled={!enf.pinned_backup_id} title="Unpin">⊘ Unpin</button>
          </div>
        </div>

        {enf.mode === "enforce" && (
          <div style="margin-top:12px;padding:10px 14px;border-radius:var(--radius-sm);background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.25);font-size:13px;color:var(--green);display:flex;align-items:center;gap:8px">
            <span class="pulse-dot green" /> Auto-enforcement active — drift is checked every {enf.check_interval}s and restored automatically.
          </div>
        )}

        {/* Drift result with dual-perspective view */}
        {driftResult && (
          <div style="margin-top:14px;padding:14px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--surface-raised)">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <span class={`voter-heartbeat ${driftResult.drift_detected ? "down" : "up"}`} style="animation:none" />
              <span style="font-weight:600;font-size:14px">{driftResult.drift_detected ? "Drift Detected" : "No Drift"}</span>
              {driftResult.action && driftResult.action !== "none" && (
                <span class={`scope-badge ${driftResult.action === "restored" ? "enabled" : "disabled"}`} style="margin:0">{driftResult.action}</span>
              )}
            </div>

            {driftResult.drift_detected && driftResult.total_changes > 0 && (
              <>
                {/* Tabs for dual-perspective */}
                <div class="modal-tabs" style="margin-bottom:10px;border-bottom:1px solid var(--border)">
                  <button class={`modal-tab${driftTab === "what_changed" ? " active" : ""}`} onClick={() => setDriftTab("what_changed")}>
                    What Changed ({driftResult.drift_summary?.length ?? 0})
                  </button>
                  <button class={`modal-tab${driftTab === "restore_plan" ? " active" : ""}`} onClick={() => setDriftTab("restore_plan")}>
                    Restore Plan ({driftResult.changes?.length ?? 0})
                  </button>
                </div>

                <ChangesTable changes={driftTab === "what_changed" ? (driftResult.drift_summary ?? []) : driftResult.changes} />

                <div style="display:flex;gap:8px;margin-top:10px;align-items:center">
                  {enf.mode === "enforce" && (
                    <span style="font-size:11px;color:var(--green)">⚡ Will auto-restore on next cycle</span>
                  )}
                  {enf.mode !== "enforce" && (
                    <button class="btn btn-sm btn-danger" onClick={restoreFromPinned} disabled={!driftResult.drift_detected}>Restore Pinned State</button>
                  )}
                  <button class="btn btn-sm btn-success" onClick={acceptDrift} disabled={!driftResult.drift_detected}>Accept Drift</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}

// -- Changes Table (reusable) ------------------------------------------------

function ChangesTable({ changes }: { changes: DriftChange[] }) {
  if (!changes.length) return <div style="font-size:12px;color:var(--text-dim)">No changes</div>;

  return (
    <div class="card" style="max-height:250px;overflow-y:auto;padding:0">
      <table>
        <thead><tr><th>Scope</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>
          {changes.map((c, i) => (
            <tr key={i}>
              <td>{c.scope || "—"}</td>
              <td><span class={`scope-badge ${actionBadgeClass(c.action)}`} style="margin:0;font-size:10px">{c.action}</span></td>
              <td style="font-size:12px;font-family:var(--mono)">{c.detail || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function actionBadgeClass(action: string): string {
  if (action.includes("added")) return "enabled";
  if (action.includes("deleted") || action.includes("removed")) return "disabled";
  return "disabled"; // setting_changed, etc.
}

// -- Settings Section --------------------------------------------------------

function SettingsSection() {
  const bs = backupSettings.value;
  const enf = enforcement.value;
  const [bForm, setBForm] = useState({ cron_schedule: "", max_backups: 50 });
  const [eForm, setEForm] = useState({ check_interval: 300, auto_restore_cooldown: 60, max_history: 50, backup_on_pin: true });

  useEffect(() => {
    setBForm({ cron_schedule: bs.cron_schedule || "0 * * * *", max_backups: bs.max_backups });
    setEForm({
      check_interval: enf.check_interval ?? 300,
      auto_restore_cooldown: enf.auto_restore_cooldown ?? 60,
      max_history: enf.max_history ?? 50,
      backup_on_pin: enf.backup_on_pin ?? true,
    });
  }, [bs, enf]);

  async function toggleAutoBackup() {
    try {
      backupSettings.value = await api.updateBackupSettings({ auto_enabled: !bs.auto_enabled });
      toast(`Auto-backup ${!bs.auto_enabled ? "enabled" : "disabled"}`, "success");
      await loadBackupSettings();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
  }

  async function saveBackup() {
    try {
      backupSettings.value = await api.updateBackupSettings(bForm);
      toast("Backup settings saved", "success"); await loadBackupSettings();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
  }

  async function saveEnforcement() {
    try {
      await api.updateEnforcementSettings(eForm);
      toast("Enforcement settings saved", "success"); await loadEnforcement();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
  }

  return (
    <>
      <div class="section-title fade-up fade-up-2">⚙️ Settings</div>
      <div class="config-grid fade-up fade-up-2" style="grid-template-columns:1fr 1fr;margin-bottom:24px">
        {/* Backup Settings Card */}
        <div class="card" style="padding:20px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
            <span style="font-size:16px">💾</span>
            <span style="font-weight:600;font-size:14px">Backup</span>
            <span style="margin-left:auto;font-size:11px;color:var(--text-dim)">{bs.stored_backups} stored</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:14px">
            <div class="toggle" onClick={toggleAutoBackup} style="cursor:pointer">
              <div class={`toggle-track${bs.auto_enabled ? " on" : ""}`}><div class="toggle-knob" /></div>
              <span>Scheduled Backups</span>
            </div>
            <div class="form-row" style="gap:12px">
              <div class="form-field" style="flex:2">
                <label>Cron Schedule</label>
                <input type="text" value={bForm.cron_schedule} disabled={!bs.auto_enabled}
                  onInput={(e) => setBForm({ ...bForm, cron_schedule: (e.target as HTMLInputElement).value })}
                  placeholder="0 * * * *" style="font-family:var(--mono);font-size:13px" />
                <span style="font-size:10px;color:var(--text-dim);margin-top:2px">min hour dom mon dow</span>
              </div>
              <div class="form-field">
                <label>Max Retained</label>
                <input type="number" min="1" max="500" value={bForm.max_backups}
                  onInput={(e) => setBForm({ ...bForm, max_backups: parseInt((e.target as HTMLInputElement).value) || 1 })} />
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:12px">
              <button class="btn btn-sm btn-primary" onClick={saveBackup}>Save</button>
              {bs.next_run > 0 && <span style="font-size:11px;color:var(--text-dim)">Next: {formatTime(bs.next_run)}</span>}
            </div>
          </div>
        </div>

        {/* Enforcement Settings Card */}
        <div class="card" style="padding:20px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
            <span style="font-size:16px">🛡️</span>
            <span style="font-weight:600;font-size:14px">Drift Detection</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:14px">
            <div class="form-row" style="gap:12px">
              <div class="form-field"><label>Check Interval (sec)</label>
                <input type="number" min="30" value={eForm.check_interval}
                  onInput={(e) => setEForm({ ...eForm, check_interval: parseInt((e.target as HTMLInputElement).value) || 30 })} />
              </div>
              <div class="form-field"><label>Restore Cooldown (sec)</label>
                <input type="number" min="0" value={eForm.auto_restore_cooldown}
                  onInput={(e) => setEForm({ ...eForm, auto_restore_cooldown: parseInt((e.target as HTMLInputElement).value) || 0 })} />
              </div>
            </div>
            <div class="form-row" style="gap:12px">
              <div class="form-field"><label>History Limit</label>
                <input type="number" min="1" max="500" value={eForm.max_history}
                  onInput={(e) => setEForm({ ...eForm, max_history: parseInt((e.target as HTMLInputElement).value) || 1 })} />
              </div>
            </div>
            <div class="toggle" onClick={() => setEForm({ ...eForm, backup_on_pin: !eForm.backup_on_pin })} style="cursor:pointer">
              <div class={`toggle-track${eForm.backup_on_pin ? " on" : ""}`}><div class="toggle-knob" /></div>
              <span>Backup Before Pin</span>
            </div>
            <button class="btn btn-sm btn-primary" onClick={saveEnforcement} style="align-self:flex-start">Save</button>
          </div>
        </div>
      </div>
    </>
  );
}

// -- Drift History -----------------------------------------------------------

const driftDetail = signal<DriftEvent | null>(null);

function DriftHistory() {
  const history = enforcement.value.history ?? [];
  const hp = enforcement.value.history_pagination;
  if (!history.length && (!hp || hp.total === 0)) return null;

  return (
    <div class="fade-up fade-up-3" style="margin-bottom:24px">
      <div class="section-title">📋 Drift History</div>
      <div class="timeline">
        {history.map((e) => (
          <div key={e.detected_at} class="timeline-item" style="cursor:pointer"
            onClick={() => { driftDetail.value = e; openModal("drift-detail"); }}>
            <div class="timeline-meta">
              <span class="timeline-time">{formatTime(e.detected_at)}</span>
              <span class={`voter-status ${e.action_taken === "restored" ? "up" : "down"}`}>{e.action_taken}</span>
              <span style="font-size:12px;color:var(--text-dim)">{e.change_count} change(s)</span>
              {(e.count ?? 1) > 1 && (
                <span style="font-size:11px;background:var(--surface-raised);border:1px solid var(--border);border-radius:10px;padding:1px 8px;color:var(--yellow)">
                  ×{e.count} <span style="color:var(--text-dim);font-size:10px">last {formatTime(e.last_seen)}</span>
                </span>
              )}
              <span style="font-size:11px;color:var(--text-dim);margin-left:auto;font-family:var(--mono)">{e.backup_id}</span>
            </div>
          </div>
        ))}
      </div>
      {hp && (
        <Paginator total={hp.total} offset={hp.offset} limit={hp.limit}
          onPage={(o) => { historyOffset.value = o; loadEnforcement(); }}
        />
      )}
    </div>
  );
}

// -- Drift Detail Modal (with dual view) -------------------------------------

function DriftDetailModal() {
  const [tab, setTab] = useState<"what_changed" | "restore_plan">("what_changed");
  const e = driftDetail.value;

  async function restoreAndClose() {
    if (!enforcement.value.pinned_backup_id) return;
    try {
      const d = await api.restoreBackup(enforcement.value.pinned_backup_id, false);
      toast(`Restored: ${d.total_changes} change(s) applied`, "success");
      closeModal(); await loadEnforcement();
    } catch (err) { toast((err as Error).message, "error"); }
  }

  async function acceptAndClose() {
    try {
      const d = await api.acceptDrift();
      toast(d.message, "success");
      closeModal(); await loadEnforcement(); await loadBackups();
    } catch (err) { toast((err as Error).message, "error"); }
  }

  return (
    <Modal name="drift-detail" width="700px">
      {e && (
        <>
          <h2>Drift Event</h2>
          <div class="card" style="margin-bottom:14px">
            <table>
              <tr><td style="color:var(--text-dim);width:35%">Detected</td><td class="mono">{formatTime(e.detected_at)}</td></tr>
              <tr><td style="color:var(--text-dim)">Compared Against</td><td class="mono">{e.backup_id}</td></tr>
              <tr><td style="color:var(--text-dim)">Action Taken</td><td><span class={`voter-status ${e.action_taken === "restored" ? "up" : "down"}`}>{e.action_taken}</span></td></tr>
              <tr><td style="color:var(--text-dim)">Changes</td><td>{e.change_count}</td></tr>
              {(e.count ?? 1) > 1 && (
                <tr><td style="color:var(--text-dim)">Occurrences</td><td>{e.count} <span style="font-size:11px;color:var(--text-dim)">(first {formatTime(e.detected_at)}, last {formatTime(e.last_seen)})</span></td></tr>
              )}
            </table>
          </div>

          {/* Dual-perspective tabs */}
          <div class="modal-tabs" style="margin-bottom:10px">
            <button class={`modal-tab${tab === "what_changed" ? " active" : ""}`} onClick={() => setTab("what_changed")}>
              What Changed ({e.drift_summary?.length ?? 0})
            </button>
            <button class={`modal-tab${tab === "restore_plan" ? " active" : ""}`} onClick={() => setTab("restore_plan")}>
              Restore Plan ({e.changes?.length ?? 0})
            </button>
          </div>

          <div style="max-height:350px;overflow-y:auto;margin-bottom:18px">
            <ChangesTable changes={tab === "what_changed" ? (e.drift_summary ?? []) : e.changes} />
          </div>

          {e.action_taken !== "restored" ? (
            <div style="display:flex;gap:12px;justify-content:flex-end">
              {enforcement.value.mode !== "enforce" && (
                <button class="btn btn-danger" onClick={restoreAndClose}>Restore Pinned State</button>
              )}
              {enforcement.value.mode === "enforce" && (
                <div style="font-size:12px;color:var(--green);text-align:right">⚡ Enforce mode active — drift will be auto-restored on next check cycle.</div>
              )}
              <button class="btn btn-success" onClick={acceptAndClose}>Accept Drift (Pin Current)</button>
            </div>
          ) : (
            <div style="font-size:12px;color:var(--text-dim);text-align:right">This drift was auto-restored.</div>
          )}
        </>
      )}
    </Modal>
  );
}

// -- Backups Table -----------------------------------------------------------

function BackupsTable() {
  const [creating, setCreating] = useState(false);
  const enf = enforcement.value;

  async function create() {
    setCreating(true);
    try {
      const d = await api.createBackup();
      toast(`Backup created: ${d.backup_id}`, "success");
      await loadBackups(); await loadBackupSettings();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
    setCreating(false);
  }

  async function pin(id: string) {
    try { await api.pinBackup(id); toast(`Pinned: ${id}`, "success"); await loadEnforcement(); await loadBackups(); }
    catch (e) { toast((e as Error).message, "error"); }
  }

  function confirmDelete(b: BackupManifest) {
    const isPinned = b.backup_id === enf.pinned_backup_id;
    showConfirm("Delete Backup", `Delete backup "${b.backup_id}"?${isPinned ? " ⚠️ This is the pinned backup!" : ""}`, "Delete", async () => {
      try {
        if (isPinned) await api.unpinBackup();
        await api.deleteBackup(b.backup_id);
        toast("Backup deleted", "success"); closeModal(); await loadBackups(); await loadEnforcement();
      } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
    });
  }

  return (
    <>
      <div class="section-title fade-up fade-up-3">
        💾 Backups
        <div class="actions"><button class="btn btn-primary" onClick={create} disabled={creating}>{creating ? "Creating…" : "+ New Backup"}</button></div>
      </div>
      <div class="fade-up fade-up-4">
        <div class="card" style="overflow-x:auto">
          <table>
            <thead><tr><th>ID</th><th>Created</th><th>Source</th><th>Scopes</th><th>Reservations</th><th>Description</th><th></th></tr></thead>
            <tbody>
              {backups.value.map((b) => (
                <tr key={b.backup_id}>
                  <td class="mono" style="white-space:nowrap">
                    {b.backup_id}
                    {b.backup_id === enf.pinned_backup_id && <span style="color:var(--green);font-size:11px;margin-left:4px">📌 pinned</span>}
                  </td>
                  <td class="mono" style="white-space:nowrap">{formatTime(b.created_at)}</td>
                  <td class="mono" style="font-size:11px">{b.source.replace("https://", "")}</td>
                  <td style="text-align:center">{b.scope_count}</td>
                  <td style="text-align:center">{b.reservation_count}</td>
                  <td>{b.description || "—"}</td>
                  <td style="white-space:nowrap">
                    <button class="btn btn-sm btn-success" onClick={() => pin(b.backup_id)} disabled={b.backup_id === enf.pinned_backup_id} title="Pin">📌</button>
                    {" "}
                    <button class="btn btn-sm" onClick={() => { restoreTarget.value = b; openModal("restore"); }} title="Restore">⏪</button>
                    {" "}
                    <button class="btn btn-sm btn-danger" onClick={() => confirmDelete(b)} title="Delete">✕</button>
                  </td>
                </tr>
              ))}
              {backups.value.length === 0 && <tr><td colSpan={7} style="text-align:center;color:var(--text-dim)">No backups yet — create one to get started</td></tr>}
            </tbody>
          </table>
          <Paginator
            total={backupsPagination.value.total}
            offset={backupsPagination.value.offset}
            limit={backupsPagination.value.limit}
            onPage={(o) => { backupsPagination.value = { ...backupsPagination.value, offset: o }; loadBackups(); }}
          />
        </div>
      </div>
    </>
  );
}

// -- Restore Modal -----------------------------------------------------------

const restoreTarget = signal<BackupManifest | null>(null);

function RestoreModal() {
  const [preview, setPreview] = useState<{ changes: DriftChange[]; total_changes: number } | null>(null);
  const [restoring, setRestoring] = useState(false);
  const b = restoreTarget.value;

  async function doPreview() {
    if (!b) return; setRestoring(true);
    try { setPreview(await api.restoreBackup(b.backup_id, true)); }
    catch (e) { toast((e as Error).message, "error"); }
    setRestoring(false);
  }

  async function doRestore() {
    if (!b) return; setRestoring(true);
    try {
      const d = await api.restoreBackup(b.backup_id, false);
      toast(`Restored: ${d.total_changes} change(s) applied`, "success");
      closeModal();
    } catch (e) { if (!isAuthCancelled(e)) toast((e as Error).message, "error"); }
    setRestoring(false);
  }

  return (
    <Modal name="restore" width="600px">
      <h2>Restore Backup</h2>
      {b && (
        <>
          <div class="card" style="margin-bottom:14px">
            <table>
              <tr><td style="color:var(--text-dim);width:40%">Backup ID</td><td class="mono">{b.backup_id}</td></tr>
              <tr><td style="color:var(--text-dim)">Created</td><td class="mono">{formatTime(b.created_at)}</td></tr>
              <tr><td style="color:var(--text-dim)">Scopes</td><td>{b.scope_count}</td></tr>
              <tr><td style="color:var(--text-dim)">Reservations</td><td>{b.reservation_count}</td></tr>
            </table>
          </div>
          {preview && (
            <div style="margin-bottom:14px">
              <div style="font-size:13px;font-weight:600;margin-bottom:8px">
                {preview.total_changes > 0
                  ? <span style="color:#f97316">{preview.total_changes} change(s) detected</span>
                  : <span style="color:var(--green)">No changes needed — state matches backup</span>}
              </div>
              {preview.changes.length > 0 && <ChangesTable changes={preview.changes} />}
            </div>
          )}
          <div style="display:flex;gap:12px;justify-content:flex-end">
            <button class="btn" onClick={doPreview} disabled={restoring}>Preview Changes</button>
            <button class="btn btn-danger" onClick={doRestore} disabled={restoring}>{restoring ? "Restoring…" : "Apply Restore"}</button>
          </div>
        </>
      )}
    </Modal>
  );
}

// -- Mount -------------------------------------------------------------------

const root = document.getElementById("app");
if (root) render(<ProtectionPage />, root);

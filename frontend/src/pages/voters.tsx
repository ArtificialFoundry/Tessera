/** Voters management page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import {
  api,
  type VoterInfoDetail,
  type VoterListResponse,
  type FailoverStatus,
  type VoterInfo,
  type RegistrationTokenListResponse,
  type RegistrationTokenInfo,
} from "@/lib/api";
import { toast, timeAgo, formatTime, poll } from "@/lib/utils";
import { Shell, showConfirm } from "@/components/Shell";
import "@/styles/tessera.css";

// -- State -------------------------------------------------------------------

const voters = signal<VoterListResponse | null>(null);
const failover = signal<FailoverStatus | null>(null);
const tokens = signal<RegistrationTokenListResponse | null>(null);
const busy = signal<string | null>(null);
const newPsk = signal<{ name: string; psk: string } | null>(null);
const newToken = signal<string | null>(null);
const tokenIp = signal("");
const tokenTtl = signal("");

async function refresh() {
  try {
    const [v, f, t] = await Promise.all([api.listVoters(), api.status(), api.listTokens()]);
    voters.value = v;
    failover.value = f;
    tokens.value = t;
  } catch { /* ping handles connectivity */ }
}

const poller = poll(async () => { await refresh(); return null; }, signal(null), 5_000);

// -- Merged voter model ------------------------------------------------------

interface MergedVoter {
  name: string;
  /** Registry info (null if config-only voter) */
  registry: VoterInfoDetail | null;
  /** Live failover vote info (null if not currently voting) */
  live: (VoterInfo & { name: string }) | null;
  source: "registry" | "config-only" | "both";
}

function mergeVoters(reg: VoterInfoDetail[], live: Record<string, VoterInfo>): MergedVoter[] {
  const merged = new Map<string, MergedVoter>();

  for (const v of reg) {
    merged.set(v.name, { name: v.name, registry: v, live: null, source: "registry" });
  }

  for (const [name, info] of Object.entries(live)) {
    const existing = merged.get(name);
    if (existing) {
      existing.live = { name, ...info };
      existing.source = "both";
    } else {
      merged.set(name, { name, registry: null, live: { name, ...info }, source: "config-only" });
    }
  }

  // Sort: pending first, then by name
  return [...merged.values()].sort((a, b) => {
    const aP = a.registry?.status === "pending" ? 0 : 1;
    const bP = b.registry?.status === "pending" ? 0 : 1;
    if (aP !== bP) return aP - bP;
    return a.name.localeCompare(b.name);
  });
}

// -- Helpers -----------------------------------------------------------------

function isLiveOnline(v: VoterInfo | null): boolean {
  if (!v?.received_at) return false;
  return Date.now() / 1000 - v.received_at <= 120;
}

function liveStatusDot(live: VoterInfo | null): string {
  if (!live) return "dormant";
  if (!isLiveOnline(live)) return "offline";
  return live.status === "up" ? "online" : "warn";
}

function liveLabel(live: VoterInfo | null): string {
  if (!live) return "No votes yet";
  if (!isLiveOnline(live)) return "Offline";
  return live.status === "up" ? "Voting UP" : "Voting DOWN";
}

function adminStatusColor(s: string | undefined): string {
  if (s === "approved") return "var(--green)";
  if (s === "pending") return "var(--yellow)";
  if (s === "revoked") return "var(--red)";
  return "var(--text-dim)";
}

function adminBadge(v: MergedVoter) {
  if (!v.registry) return <span class="badge badge-dim">config-only</span>;
  const s = v.registry.status;
  return <span class="voter-status" style={`color:${adminStatusColor(s)}`}>{s}</span>;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "success");
  } catch {
    toast("Copy failed", "error");
  }
}

// -- Actions -----------------------------------------------------------------

async function approveVoter(name: string) {
  busy.value = name;
  try {
    const r = await api.approveVoter(name);
    toast(`${r.voter_name} approved`, "success");
    if (r.psk) newPsk.value = { name: r.voter_name, psk: r.psk };
    await refresh();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function revokeVoter(name: string) {
  busy.value = name;
  try {
    const r = await api.revokeVoter(name);
    toast(`${r.voter_name} revoked`, "success");
    await refresh();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function deleteVoter(name: string) {
  busy.value = name;
  try {
    await api.deleteVoter(name);
    toast(`${name} deleted`, "success");
    await refresh();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function rotateKey(name: string) {
  busy.value = name;
  try {
    const r = await api.rotateVoterKey(name);
    newPsk.value = { name: r.voter_name, psk: r.new_psk };
    toast(r.message, "success");
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function generateToken() {
  busy.value = "gen-token";
  try {
    const opts: { bind_ip?: string; ttl?: number } = {};
    if (tokenIp.value.trim()) opts.bind_ip = tokenIp.value.trim();
    if (tokenTtl.value.trim()) opts.ttl = parseInt(tokenTtl.value.trim(), 10);
    const r = await api.generateToken(opts);
    newToken.value = r.token;
    toast("Token generated", "success");
    tokenIp.value = "";
    tokenTtl.value = "";
    await refresh();
  } catch (e: unknown) {
    toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

// -- Components --------------------------------------------------------------

function PskBanner() {
  const p = newPsk.value;
  if (!p) return null;
  return (
    <div class="card fade-up" style="border:1px solid var(--yellow);margin-bottom:16px;padding:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong>🔑 New PSK for {p.name}</strong>
        <button class="btn btn-sm btn-ghost" onClick={() => { newPsk.value = null; }}>Dismiss</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <code class="copyable-field">{p.psk}</code>
        <button class="btn btn-sm btn-accent" onClick={() => copyText(p.psk)}>Copy</button>
      </div>
      <div style="font-size:11px;color:var(--yellow);margin-top:6px">This PSK is shown once. Copy it now.</div>
    </div>
  );
}

function TokenBanner() {
  const t = newToken.value;
  if (!t) return null;
  return (
    <div class="card fade-up" style="border:1px solid var(--accent);margin-bottom:16px;padding:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong>🎫 Registration Token</strong>
        <button class="btn btn-sm btn-ghost" onClick={() => { newToken.value = null; }}>Dismiss</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <code class="copyable-field">{t}</code>
        <button class="btn btn-sm btn-accent" onClick={() => copyText(t)}>Copy</button>
      </div>
      <div style="font-size:11px;color:var(--text-dim);margin-top:6px">Give this token to the voter operator. It can only be used once.</div>
    </div>
  );
}

function VoterRow({ v }: { v: MergedVoter }) {
  const isBusy = busy.value === v.name;
  const isPending = v.registry?.status === "pending";
  const isApproved = v.registry?.status === "approved";
  const isConfigOnly = v.source === "config-only";
  const dotClass = liveStatusDot(v.live);

  return (
    <tr class={isPending ? "row-pending" : undefined}>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <span class={`dot ${dotClass}`} />
          <div>
            <strong>{v.name}</strong>
            {isConfigOnly && <div style="font-size:10px;color:var(--text-dim)">via config file</div>}
          </div>
        </div>
      </td>
      <td>{adminBadge(v)}</td>
      <td>
        <span class={`live-status ${dotClass}`}>{liveLabel(v.live)}</span>
      </td>
      <td style="font-size:12px">{v.registry?.ip_address || "—"}</td>
      <td style="font-size:12px" title={v.live?.received_at ? formatTime(v.live.received_at) : undefined}>
        {v.live?.received_at ? timeAgo(v.live.received_at) : "—"}
      </td>
      <td style="font-size:12px" title={v.registry ? formatTime(v.registry.registered_at) : undefined}>
        {v.registry ? timeAgo(v.registry.registered_at) : "—"}
      </td>
      <td>
        {isConfigOnly ? (
          <span style="font-size:11px;color:var(--text-dim)">Managed via config</span>
        ) : (
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            {isPending && (
              <>
                <button class="btn btn-xs btn-accent" disabled={isBusy} onClick={() => approveVoter(v.name)}>Approve</button>
                <button class="btn btn-xs btn-danger" disabled={isBusy} onClick={() => showConfirm("Reject Voter", `Delete pending voter ${v.name}?`, "Reject", () => deleteVoter(v.name))}>Reject</button>
              </>
            )}
            {isApproved && (
              <>
                <button class="btn btn-xs btn-ghost" disabled={isBusy} onClick={() => showConfirm("Revoke Voter", `Revoke ${v.name}? It will stop accepting their votes.`, "Revoke", () => revokeVoter(v.name))}>Revoke</button>
                <button class="btn btn-xs btn-ghost" disabled={isBusy} onClick={() => rotateKey(v.name)}>Rotate Key</button>
              </>
            )}
            {!isPending && (
              <button class="btn btn-xs btn-danger" disabled={isBusy} onClick={() => showConfirm("Delete Voter", `Permanently delete ${v.name}?`, "Delete", () => deleteVoter(v.name))}>Delete</button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function TokenRow({ t }: { t: RegistrationTokenInfo }) {
  const expired = t.expires_at > 0 && t.expires_at < Date.now() / 1000;
  return (
    <tr style={t.used || expired ? "opacity:0.5" : undefined}>
      <td>
        <code style="font-size:12px">{t.token.slice(0, 16)}…</code>
        <button class="btn btn-xs btn-ghost" style="margin-left:4px" onClick={() => copyText(t.token)}>Copy</button>
      </td>
      <td style="font-size:12px">{t.bind_ip || "any"}</td>
      <td style="font-size:12px">{formatTime(t.created_at)}</td>
      <td style="font-size:12px">{t.expires_at > 0 ? formatTime(t.expires_at) : "never"}</td>
      <td>{t.used ? <span style="color:var(--text-dim)">Used by {t.used_by}</span> : expired ? <span style="color:var(--red)">Expired</span> : <span style="color:var(--green)">Available</span>}</td>
    </tr>
  );
}

// -- Page --------------------------------------------------------------------

function VotersPage() {
  useEffect(() => { refresh(); poller.start(); return () => poller.stop(); }, []);

  const v = voters.value;
  const f = failover.value;
  const t = tokens.value;

  if (!v || !f) return <Shell activeTab="voters"><div class="empty">Loading…</div></Shell>;

  const liveVoters = f.voters ?? {};
  const merged = mergeVoters(v.voters, liveVoters);
  const pendingCount = merged.filter((m) => m.registry?.status === "pending").length;
  const approvedCount = merged.filter((m) => m.registry?.status === "approved").length;
  const onlineCount = merged.filter((m) => isLiveOnline(m.live)).length;
  const tokenList = t?.tokens ?? [];
  const activeTokens = tokenList.filter((x) => !x.used && (x.expires_at <= 0 || x.expires_at > Date.now() / 1000));

  return (
    <Shell activeTab="voters">
      <PskBanner />
      <TokenBanner />

      <div class="metrics-bar fade-up fade-up-1">
        <div class="card metric">
          <div class="metric-value">{merged.length}</div>
          <div class="metric-label">Total Voters</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--green)">{onlineCount}</div>
          <div class="metric-label">Online</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--green)">{approvedCount}</div>
          <div class="metric-label">Approved</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--yellow)">{pendingCount}</div>
          <div class="metric-label">Pending</div>
        </div>
        <div class="card metric">
          <div class="metric-value" style="color:var(--accent)">{activeTokens.length}</div>
          <div class="metric-label">Active Tokens</div>
        </div>
      </div>

      {/* Unified voter table */}
      <div class="section-title fade-up fade-up-2">🗳️ Voters</div>
      {merged.length === 0 ? (
        <div class="card empty fade-up fade-up-2">No voters yet. Generate a registration token below to onboard your first voter.</div>
      ) : (
        <div class="card fade-up fade-up-2" style="overflow-x:auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Voter</th>
                <th>Admin Status</th>
                <th>Live Status</th>
                <th>IP</th>
                <th>Last Seen</th>
                <th>Registered</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {merged.map((mv) => <VoterRow key={mv.name} v={mv} />)}
            </tbody>
          </table>
        </div>
      )}

      {/* Registration Tokens */}
      <div class="section-title fade-up fade-up-3">🎫 Registration Tokens</div>
      <div class="card fade-up fade-up-3" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
          <div>
            <label style="font-size:11px;color:var(--text-dim);display:block;margin-bottom:4px">IP Restriction</label>
            <input
              class="input"
              placeholder="any"
              value={tokenIp.value}
              onInput={(e) => { tokenIp.value = (e.target as HTMLInputElement).value; }}
              style="width:160px"
            />
          </div>
          <div>
            <label style="font-size:11px;color:var(--text-dim);display:block;margin-bottom:4px">TTL (seconds)</label>
            <input
              class="input"
              type="number"
              placeholder="3600"
              value={tokenTtl.value}
              onInput={(e) => { tokenTtl.value = (e.target as HTMLInputElement).value; }}
              onKeyDown={(e) => { if (e.key === "Enter") generateToken(); }}
              style="width:120px"
            />
          </div>
          <button class="btn btn-accent" disabled={busy.value === "gen-token"} onClick={generateToken}>
            Generate Token
          </button>
        </div>
      </div>

      {tokenList.length === 0 ? (
        <div class="card empty fade-up fade-up-3">No tokens generated yet</div>
      ) : (
        <div class="card fade-up fade-up-3" style="overflow-x:auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Token</th>
                <th>Bind IP</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tokenList.map((tk) => <TokenRow key={tk.token} t={tk} />)}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  );
}

const root = document.getElementById("app");
if (root) render(<VotersPage />, root);

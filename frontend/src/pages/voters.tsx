/** Voters management page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useState, useCallback } from "preact/hooks";
import {
  api,
  isAuthCancelled,
  type VoterInfoDetail,
  type VoterListResponse,
  type FailoverStatus,
  type VoterInfo,
  type RegistrationTokenListResponse,
  type RegistrationTokenInfo,
} from "@/lib/api";
import { toast, timeAgo, formatTime, poll } from "@/lib/utils";
import { Shell, Modal, openModal, closeModal, showConfirm, StaleBanner } from "@/components/Shell";
import "@/styles/tessera.css";

// -- State -------------------------------------------------------------------

const voters = signal<VoterListResponse | null>(null);
const failover = signal<FailoverStatus | null>(null);
const tokens = signal<RegistrationTokenListResponse | null>(null);
const busy = signal<string | null>(null);
const newPsk = signal<{ name: string; psk: string } | null>(null);

async function refresh() {
  try {
    const [v, f, t] = await Promise.all([api.listVoters(), api.status(), api.listTokens()]);
    voters.value = v;
    failover.value = f;
    tokens.value = t;
  } catch { /* ping handles connectivity */ }
}

const poller = poll(async () => { await refresh(); return null; }, signal(null), 5_000);

// -- Enriched voter (registry + live vote overlay) ---------------------------

interface EnrichedVoter extends VoterInfoDetail {
  live: VoterInfo | null;
}

function enrichVoters(reg: VoterInfoDetail[], liveMap: Record<string, VoterInfo>): EnrichedVoter[] {
  return reg.map((v) => ({ ...v, live: liveMap[v.name] ?? null }));
}

function isOnline(v: VoterInfo | null): boolean {
  if (!v?.received_at) return false;
  return Date.now() / 1000 - v.received_at <= 120;
}

function liveDot(live: VoterInfo | null): string {
  if (!live) return "dormant";
  if (!isOnline(live)) return "offline";
  return live.status === "up" ? "online" : "warn";
}

function liveLabel(live: VoterInfo | null): string {
  if (!live) return "No votes yet";
  if (!isOnline(live)) return `Offline · last ${timeAgo(live.received_at)}`;
  return live.status === "up" ? "Voting UP" : "Voting DOWN";
}

function statusColor(s: string): string {
  if (s === "approved") return "var(--green)";
  if (s === "pending") return "var(--yellow)";
  return "var(--red)";
}

// -- Helpers -----------------------------------------------------------------

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "success");
  } catch {
    toast("Copy failed", "error");
  }
}

/** Validate IP address or CIDR client-side. */
function validateBindIp(value: string): string | null {
  if (!value.trim()) return null;
  const v = value.trim();

  // IPv4
  const ipv4Re = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
  // IPv4 CIDR
  const ipv4CidrRe = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
  // IPv6 (simplified — accept if it has colons and hex)
  const ipv6Re = /^[0-9a-fA-F:]+$/;
  // IPv6 CIDR
  const ipv6CidrRe = /^[0-9a-fA-F:]+\/\d{1,3}$/;

  if (ipv4CidrRe.test(v)) {
    const m = v.match(ipv4CidrRe)!;
    const octets = [+m[1], +m[2], +m[3], +m[4]];
    const prefix = +m[5];
    if (octets.some((o) => o > 255)) return "Each octet must be 0–255";
    if (prefix > 32) return "CIDR prefix must be /0–/32";
    return null;
  }

  if (ipv4Re.test(v)) {
    const m = v.match(ipv4Re)!;
    const octets = [+m[1], +m[2], +m[3], +m[4]];
    if (octets.some((o) => o > 255)) return "Each octet must be 0–255";
    return null;
  }

  if (ipv6CidrRe.test(v)) {
    const prefix = parseInt(v.split("/")[1], 10);
    if (prefix > 128) return "IPv6 CIDR prefix must be /0–/128";
    return null;
  }

  if (ipv6Re.test(v) && v.includes(":")) return null;

  return "Enter a valid IPv4, IPv6, or CIDR (e.g. 192.168.1.10, 10.0.0.0/24, fd00::1)";
}

/** Validate TTL string. */
function validateTtl(value: string): string | null {
  if (!value.trim()) return null;
  const n = parseInt(value.trim(), 10);
  if (isNaN(n) || n < 60) return "Minimum 60 seconds (1 minute)";
  if (n > 604800) return "Maximum 604800 seconds (7 days)";
  return null;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
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
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
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
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
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
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
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
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

async function deleteToken(prefix: string) {
  busy.value = "del-token";
  try {
    await api.deleteToken(prefix);
    toast("Token deleted", "success");
    await refresh();
  } catch (e: unknown) {
    if (!isAuthCancelled(e)) toast(String((e as Error).message), "error");
  } finally {
    busy.value = null;
  }
}

// -- Token Generation Wizard -------------------------------------------------

function TokenWizard() {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [bindIp, setBindIp] = useState("");
  const [ttl, setTtl] = useState("3600");
  const [ipError, setIpError] = useState<string | null>(null);
  const [ttlError, setTtlError] = useState<string | null>(null);
  const [ipTouched, setIpTouched] = useState(false);
  const [ttlTouched, setTtlTouched] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [tokenMeta, setTokenMeta] = useState<{ expiresAt: number; bindIp: string | null } | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setStep(1);
    setBindIp("");
    setTtl("3600");
    setIpError(null);
    setTtlError(null);
    setIpTouched(false);
    setTtlTouched(false);
    setGenerating(false);
    setGeneratedToken(null);
    setTokenMeta(null);
    setServerError(null);
  }, []);

  function handleIpBlur() {
    setIpTouched(true);
    setIpError(validateBindIp(bindIp));
  }

  function handleTtlBlur() {
    setTtlTouched(true);
    setTtlError(validateTtl(ttl));
  }

  function canProceedStep1(): boolean {
    const ipOk = !validateBindIp(bindIp);
    const ttlOk = !validateTtl(ttl);
    return ipOk && ttlOk;
  }

  function goToReview() {
    // Final validation
    const ie = validateBindIp(bindIp);
    const te = validateTtl(ttl);
    setIpError(ie);
    setTtlError(te);
    setIpTouched(true);
    setTtlTouched(true);
    if (!ie && !te) setStep(2);
  }

  async function handleGenerate() {
    setGenerating(true);
    setServerError(null);
    try {
      const opts: { bind_ip?: string; ttl?: number } = {};
      if (bindIp.trim()) opts.bind_ip = bindIp.trim();
      if (ttl.trim()) opts.ttl = parseInt(ttl.trim(), 10);
      const r = await api.generateToken(opts);
      setGeneratedToken(r.token);
      setTokenMeta({ expiresAt: r.expires_at, bindIp: r.bind_ip });
      setStep(3);
      await refresh();
    } catch (e: unknown) {
      setServerError(String((e as Error).message));
    } finally {
      setGenerating(false);
    }
  }

  function handleClose() {
    reset();
    closeModal();
  }

  const ttlNum = parseInt(ttl.trim() || "3600", 10);

  return (
    <Modal name="token-wizard" width="520px" onClose={reset}>
      {/* Step indicator */}
      <div class="wizard-steps">
        <div class={`wizard-step ${step >= 1 ? "active" : ""} ${step > 1 ? "done" : ""}`}>
          <div class="wizard-step-num">{step > 1 ? "✓" : "1"}</div>
          <span>Configure</span>
        </div>
        <div class="wizard-step-line" />
        <div class={`wizard-step ${step >= 2 ? "active" : ""} ${step > 2 ? "done" : ""}`}>
          <div class="wizard-step-num">{step > 2 ? "✓" : "2"}</div>
          <span>Review</span>
        </div>
        <div class="wizard-step-line" />
        <div class={`wizard-step ${step >= 3 ? "active" : ""}`}>
          <div class="wizard-step-num">3</div>
          <span>Token</span>
        </div>
      </div>

      {/* Step 1: Configure */}
      {step === 1 && (
        <div class="wizard-body">
          <h3 style="margin:0 0 4px">Configure Token</h3>
          <p class="wizard-desc">
            Registration tokens allow new voters to join your cluster.
            Each token can only be used once.
          </p>

          <div class="form-field" style="margin-bottom:16px">
            <label>
              Bind Address
              <span class="form-optional">optional</span>
            </label>
            <input
              class={`input ${ipTouched && ipError ? "input-error" : ipTouched && bindIp.trim() ? "input-ok" : ""}`}
              placeholder="e.g. 192.168.1.10 or 10.0.0.0/24"
              value={bindIp}
              onInput={(e) => { setBindIp((e.target as HTMLInputElement).value); if (ipTouched) setIpError(validateBindIp((e.target as HTMLInputElement).value)); }}
              onBlur={handleIpBlur}
            />
            {ipTouched && ipError && <div class="field-error">{ipError}</div>}
            {ipTouched && !ipError && bindIp.trim() && <div class="field-ok">✓ Valid {bindIp.includes("/") ? "CIDR range" : "IP address"}</div>}
            <div class="field-hint">
              Restrict which IP can use this token. Accepts:
              <ul style="margin:4px 0 0;padding-left:18px;font-size:11px">
                <li><code>192.168.1.10</code> — single IPv4</li>
                <li><code>10.0.0.0/24</code> — IPv4 CIDR range</li>
                <li><code>fd00::1</code> — single IPv6</li>
                <li><code>fd00::/64</code> — IPv6 CIDR range</li>
              </ul>
              Leave empty to allow registration from any IP. The restriction persists on the voter after registration.
            </div>
          </div>

          <div class="form-field" style="margin-bottom:20px">
            <label>
              Expiry
              <span class="form-optional">default: 1 hour</span>
            </label>
            <div style="display:flex;align-items:center;gap:10px">
              <input
                class={`input ${ttlTouched && ttlError ? "input-error" : ttlTouched && ttl.trim() ? "input-ok" : ""}`}
                type="number"
                placeholder="3600"
                value={ttl}
                min="60"
                max="604800"
                onInput={(e) => { setTtl((e.target as HTMLInputElement).value); if (ttlTouched) setTtlError(validateTtl((e.target as HTMLInputElement).value)); }}
                onBlur={handleTtlBlur}
                style="width:140px"
              />
              <span class="field-preview">
                {!isNaN(ttlNum) && ttlNum >= 60 ? formatDuration(ttlNum) : "—"}
              </span>
            </div>
            {ttlTouched && ttlError && <div class="field-error">{ttlError}</div>}
            {ttlTouched && !ttlError && ttl.trim() && <div class="field-ok">✓ Expires in {formatDuration(ttlNum)}</div>}
            <div class="field-hint">
              How long the token remains valid. Between 60s (1 min) and 604,800s (7 days).
            </div>
          </div>

          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn btn-ghost" onClick={handleClose}>Cancel</button>
            <button class="btn btn-accent" onClick={goToReview} disabled={!canProceedStep1()}>
              Review →
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Review */}
      {step === 2 && (
        <div class="wizard-body">
          <h3 style="margin:0 0 4px">Review & Generate</h3>
          <p class="wizard-desc">Confirm these settings before generating the token.</p>

          <div class="review-grid">
            <div class="review-item">
              <div class="review-label">Bind Address</div>
              <div class="review-value">
                {bindIp.trim() ? (
                  <>
                    <code>{bindIp.trim()}</code>
                    <span class="review-note">
                      Only voters connecting from {bindIp.includes("/") ? "this range" : "this IP"} can register.
                      This restriction persists after registration — votes from other IPs will be rejected.
                    </span>
                  </>
                ) : (
                  <span style="color:var(--text-dim)">Any IP — no restriction</span>
                )}
              </div>
            </div>
            <div class="review-item">
              <div class="review-label">Token Expiry</div>
              <div class="review-value">
                <strong>{formatDuration(ttlNum)}</strong>
                <span class="review-note">Token must be used within this window. After that, it's invalidated.</span>
              </div>
            </div>
            <div class="review-item">
              <div class="review-label">Usage</div>
              <div class="review-value">
                <strong>Single use</strong>
                <span class="review-note">Once a voter registers with this token, it cannot be reused.</span>
              </div>
            </div>
          </div>

          {serverError && <div class="field-error" style="margin-bottom:12px">{serverError}</div>}

          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn btn-ghost" onClick={() => setStep(1)}>← Back</button>
            <button class="btn btn-accent" onClick={handleGenerate} disabled={generating}>
              {generating ? "Generating…" : "Generate Token"}
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Token output */}
      {step === 3 && generatedToken && (
        <div class="wizard-body">
          <h3 style="margin:0 0 4px">✅ Token Generated</h3>
          <p class="wizard-desc">
            Copy this token and give it to the voter operator. It is shown <strong>once</strong> and cannot be retrieved later.
          </p>

          <div class="token-output">
            <code class="token-value">{generatedToken}</code>
            <button class="btn btn-sm btn-accent" onClick={() => copyText(generatedToken)}>Copy</button>
          </div>

          <div class="review-grid" style="margin-top:16px">
            {tokenMeta?.bindIp && (
              <div class="review-item">
                <div class="review-label">Bind Address</div>
                <div class="review-value"><code>{tokenMeta.bindIp}</code></div>
              </div>
            )}
            <div class="review-item">
              <div class="review-label">Expires</div>
              <div class="review-value">{formatTime(tokenMeta!.expiresAt)}</div>
            </div>
          </div>

          <div class="wizard-instructions" style="margin-top:16px">
            <div class="wizard-instructions-title">Next steps for the voter operator:</div>
            <ol>
              <li>Configure the voter agent with the Tessera server URL</li>
              <li>Set the registration token in the voter config</li>
              <li>Start the voter agent — it will register automatically</li>
              <li>Once registered, come back here to approve the voter</li>
            </ol>
          </div>

          <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
            <button class="btn btn-ghost" onClick={() => { reset(); }}>Generate Another</button>
            <button class="btn btn-accent" onClick={handleClose}>Done</button>
          </div>
        </div>
      )}
    </Modal>
  );
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

function VoterRow({ v }: { v: EnrichedVoter }) {
  const isBusy = busy.value === v.name;
  const isPending = v.status === "pending";
  const isApproved = v.status === "approved";
  const dot = liveDot(v.live);

  return (
    <tr class={isPending ? "row-pending" : undefined}>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <span class={`dot ${dot}`} />
          <strong>{v.name}</strong>
        </div>
      </td>
      <td><span class="voter-status" style={`color:${statusColor(v.status)}`}>{v.status}</span></td>
      <td><span class={`live-status ${dot}`}>{liveLabel(v.live)}</span></td>
      <td style="font-size:12px">{v.ip_address || "—"}</td>
      <td style="font-size:12px">{v.bind_ip ? <code style="font-size:11px">{v.bind_ip}</code> : <span style="color:var(--text-dim)">any</span>}</td>
      <td style="font-size:12px" title={formatTime(v.registered_at)}>{timeAgo(v.registered_at)}</td>
      <td>
        <div style="display:flex;gap:4px;flex-wrap:wrap">
          {isPending && (
            <>
              <button class="btn btn-xs btn-accent" disabled={isBusy} onClick={() => approveVoter(v.name)}>Approve</button>
              <button class="btn btn-xs btn-danger" disabled={isBusy} onClick={() => showConfirm("Reject Voter", `Delete pending voter ${v.name}?`, "Reject", () => deleteVoter(v.name))}>Reject</button>
            </>
          )}
          {isApproved && (
            <>
              <button class="btn btn-xs btn-ghost" disabled={isBusy} onClick={() => showConfirm("Revoke Voter", `Revoke ${v.name}? Their votes will no longer be accepted.`, "Revoke", () => revokeVoter(v.name))}>Revoke</button>
              <button class="btn btn-xs btn-ghost" disabled={isBusy} onClick={() => rotateKey(v.name)}>Rotate Key</button>
            </>
          )}
          {!isPending && (
            <button class="btn btn-xs btn-danger" disabled={isBusy} onClick={() => showConfirm("Delete Voter", `Permanently delete ${v.name}?`, "Delete", () => deleteVoter(v.name))}>Delete</button>
          )}
        </div>
      </td>
    </tr>
  );
}

function TokenRow({ t }: { t: RegistrationTokenInfo }) {
  const expired = t.expires_at > 0 && t.expires_at < Date.now() / 1000;
  const canDelete = !t.used;
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
      <td>
        {canDelete && (
          <button class="btn btn-xs btn-danger" disabled={busy.value === "del-token"} onClick={() => showConfirm("Delete Token", "Delete this registration token?", "Delete", () => deleteToken(t.token))}>Delete</button>
        )}
      </td>
    </tr>
  );
}

// -- Page --------------------------------------------------------------------

function VotersPage() {
  useEffect(() => { refresh(); poller.start(); return () => poller.stop(); }, []);

  const v = voters.value;
  const f = failover.value;
  const t = tokens.value;

  if (!v) return <Shell activeTab="voters"><div class="empty">Loading…</div></Shell>;

  const liveMap = f?.voters ?? {};
  const enriched = enrichVoters(v.voters, liveMap);
  const approvedCount = enriched.filter((x) => x.status === "approved").length;
  const pendingCount = enriched.filter((x) => x.status === "pending").length;
  const onlineCount = enriched.filter((x) => isOnline(x.live)).length;
  const tokenList = t?.tokens ?? [];
  const activeTokens = tokenList.filter((x) => !x.used && (x.expires_at <= 0 || x.expires_at > Date.now() / 1000));

  return (
    <Shell activeTab="voters">
      <StaleBanner consecutiveErrors={poller.consecutiveErrors} />
      <PskBanner />
      <TokenWizard />

      <div class="metrics-bar fade-up fade-up-1">
        <div class="card metric">
          <div class="metric-value">{enriched.length}</div>
          <div class="metric-label">Registered</div>
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

      {/* Voter table */}
      <div class="section-title fade-up fade-up-2">🗳️ Voters</div>
      {enriched.length === 0 ? (
        <div class="card empty fade-up fade-up-2">No voters registered yet. Generate a token below to onboard your first voter.</div>
      ) : (
        <div class="card fade-up fade-up-2" style="overflow-x:auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Voter</th>
                <th>Status</th>
                <th>Health</th>
                <th>IP</th>
                <th>Bind</th>
                <th>Registered</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {enriched.map((ev) => <VoterRow key={ev.name} v={ev} />)}
            </tbody>
          </table>
        </div>
      )}

      {/* Registration Tokens */}
      <div class="section-title fade-up fade-up-3" style="display:flex;align-items:center;justify-content:space-between">
        <span>🎫 Registration Tokens</span>
        <button class="btn btn-sm btn-accent" onClick={() => openModal("token-wizard")}>
          + Generate Token
        </button>
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
                <th>Actions</th>
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

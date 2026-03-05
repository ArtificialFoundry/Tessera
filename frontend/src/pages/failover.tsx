/** Failover dashboard page. */

import { render } from "preact";
import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { api, type FailoverStatus, type VoterInfo } from "@/lib/api";
import { timeAgo, formatTime, poll } from "@/lib/utils";
import { Shell, Modal, openModal, Paginator } from "@/components/Shell";
import "@/styles/tessera.css";

const status = signal<FailoverStatus | null>(null);
const voterDetail = signal<(VoterInfo & { name: string }) | null>(null);
const transOffset = signal(0);

const poller = poll(() => api.status(transOffset.value, 20), status, 5_000);

function voterClass(v: VoterInfo): string {
  if (!v.received_at || Date.now() / 1000 - v.received_at > 120) return "stale";
  return v.status;
}

function voteLabel(v: VoterInfo): string {
  if (!v.received_at || Date.now() / 1000 - v.received_at > 120) return "Offline";
  return v.status === "up" ? "Primary OK" : "Primary Unreachable";
}

function FailoverPage() {
  useEffect(() => { poller.start(); return () => poller.stop(); }, []);

  const s = status.value;
  if (!s) return <Shell activeTab="failover"><div class="empty">Loading…</div></Shell>;

  const isFailoverActive = s.state?.toLowerCase() === "active";
  const voters = s.voters ?? {};
  const voterEntries = Object.entries(voters);
  const upVoters = voterEntries.filter(([, v]) => v.status === "up" && !v.stale && v.received_at && Date.now() / 1000 - v.received_at <= 120);
  const primaryHealth = voterEntries.length === 0 ? "unknown" : upVoters.length > voterEntries.length / 2 ? "healthy" : "unhealthy";
  const quorum = s.config?.quorum ?? 1;
  const quorumPct = Math.min(100, ((s.active_votes ?? 0) / quorum) * 100);
  const quorumClass = s.has_quorum ? "ok" : quorumPct > 50 ? "warn" : "fail";

  function isActive(role: "primary" | "standby"): boolean {
    return isFailoverActive ? role === "standby" : role === "primary";
  }

  return (
    <Shell activeTab="failover">
      {/* Server Status Panel */}
      <div class="server-panel fade-up fade-up-1">
        <ServerCard
          name="dhcp-1" host="192.0.2.1" role="primary"
          isActive={isActive("primary")} isFailover={isFailoverActive}
          health={primaryHealth}
        />
        <div class="server-arrow">
          <div class="arrow-label">failover</div>
          <div class="arrow-line" />
          <div class="arrow-label">{s.state ?? "…"}</div>
        </div>
        <ServerCard
          name="dhcp-2" host="192.0.2.2" role="standby"
          isActive={isActive("standby")} isFailover={isFailoverActive}
          health="healthy"
        />
      </div>

      {/* Hero State */}
      <div class="hero card fade-up fade-up-2">
        <div class={`hero-state ${s.state ?? ""}`}>{s.state ?? "loading"}</div>
        <div class="hero-sub">
          {s.active_votes ?? 0} voters reporting · quorum {s.has_quorum ? "met ✓" : "not met"}
        </div>
      </div>

      {/* Metrics */}
      <div class="metrics-bar fade-up fade-up-3">
        <div class="card metric"><div class="metric-value" style="color:var(--green)">{s.up_count ?? 0}</div><div class="metric-label">Primary OK</div></div>
        <div class="card metric"><div class="metric-value" style="color:#f97316">{s.down_count ?? 0}</div><div class="metric-label">Primary Unreachable</div></div>
        <div class="card metric"><div class="metric-value" style="color:#f97316">{s.consecutive_down ?? 0}</div><div class="metric-label">Rounds Unreachable</div></div>
        <div class="card metric"><div class="metric-value" style="color:var(--green)">{s.consecutive_up ?? 0}</div><div class="metric-label">Rounds Healthy</div></div>
      </div>

      {/* Quorum */}
      <div class="section-title fade-up fade-up-3">📊 Quorum</div>
      <div class="card fade-up fade-up-3" style="margin-bottom:24px;padding:18px">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:12px;color:var(--text-dim)">{s.active_votes ?? 0} / {quorum} required</span>
          <span style="font-size:12px;color:var(--text-dim)">{s.has_quorum ? "✅ Quorum met" : "⏳ Waiting"}</span>
        </div>
        <div class="quorum-track"><div class={`quorum-fill ${quorumClass}`} style={`width:${quorumPct}%`} /></div>
      </div>

      {/* Voters */}
      <div class="section-title fade-up fade-up-3">🗳️ Voters</div>
      <div class="voters-grid fade-up fade-up-4">
        {voterEntries.length > 0 ? voterEntries.map(([name, v]) => (
          <div key={name} class="card voter-card" onClick={() => { voterDetail.value = { name, ...v }; openModal("voter"); }}>
            <div class="voter-name"><span class={`voter-heartbeat ${voterClass(v)}`} /> {name}</div>
            <span class={`voter-status ${voterClass(v)}`}>{voteLabel(v)}</span>
            <div class="voter-time">{timeAgo(v.received_at)}</div>
          </div>
        )) : <div class="card empty">No votes yet</div>}
      </div>

      {/* Transitions */}
      {(s.transitions_pagination?.total ?? s.transitions?.length ?? 0) > 0 && (
        <div style="margin-bottom:24px" class="fade-up fade-up-4">
          <div class="section-title">🔄 Transitions</div>
          <div class="timeline">
            {s.transitions.map((t) => (
              <div key={t.timestamp} class="timeline-item">
                <div class="timeline-meta">
                  <span class="timeline-time">{formatTime(t.timestamp)}</span>
                  <span class={`voter-status ${t.from_state}`}>{t.from_state}</span>
                  <span class="timeline-arrow">→</span>
                  <span class={`voter-status ${t.to_state}`}>{t.to_state}</span>
                </div>
                {t.reason && <div class="timeline-reason">{t.reason}</div>}
              </div>
            ))}
          </div>
          {s.transitions_pagination && (
            <Paginator
              total={s.transitions_pagination.total}
              offset={s.transitions_pagination.offset}
              limit={s.transitions_pagination.limit}
              onPage={(o) => { transOffset.value = o; }}
            />
          )}
        </div>
      )}

      {/* Config */}
      {s.config && (
        <div style="margin-bottom:24px" class="fade-up fade-up-4">
          <div class="section-title">⚙️ Configuration</div>
          <div class="config-grid">
            <div class="card config-item"><div class="config-value">{s.config.quorum}</div><div class="config-label">Quorum Threshold</div></div>
            <div class="card config-item"><div class="config-value">{s.config.failover_rounds}</div><div class="config-label">Failover Rounds</div></div>
            <div class="card config-item"><div class="config-value">{s.config.failback_rounds}</div><div class="config-label">Failback Rounds</div></div>
            <div class="card config-item"><div class="config-value">{s.config.vote_ttl}s</div><div class="config-label">Vote TTL</div></div>
          </div>
        </div>
      )}

      {/* Voter Detail Modal */}
      <Modal name="voter" width="500px">
        {voterDetail.value && (
          <>
            <h2>{voterDetail.value.name}</h2>
            <div class="voter-detail-grid">
              <div class="card voter-detail-item"><div class="config-label">Vote</div><span class={`voter-status ${voterClass(voterDetail.value)}`} style="margin-top:4px">{voteLabel(voterDetail.value)}</span></div>
              <div class="card voter-detail-item"><div class="config-label">Last Seen</div><div class="config-value" style="margin-top:4px">{timeAgo(voterDetail.value.received_at)}</div></div>
              <div class="card voter-detail-item"><div class="config-label">Received At</div><div class="config-value" style="margin-top:4px;font-size:12px">{formatTime(voterDetail.value.received_at)}</div></div>
              <div class="card voter-detail-item"><div class="config-label">Stale</div><div class="config-value" style="margin-top:4px">{voterDetail.value.stale ? "Yes" : "No"}</div></div>
            </div>
          </>
        )}
      </Modal>
    </Shell>
  );
}

// -- Server card sub-component -----------------------------------------------

interface ServerCardProps {
  name: string;
  host: string;
  role: "primary" | "standby";
  isActive: boolean;
  isFailover: boolean;
  health: string;
}

function ServerCard({ name, host, role, isActive, isFailover, health }: ServerCardProps) {
  const cardClass = [
    "card server-card",
    isActive && "active-server",
    isActive && isFailover && "failover-active",
  ].filter(Boolean).join(" ");

  const roleClass = isActive
    ? isFailover ? "role-active-failover" : "role-active"
    : role === "primary" ? "role-primary" : "role-standby";

  return (
    <div class={cardClass}>
      {isActive && (
        <div class={`active-badge ${isFailover ? "serving-failover" : "serving"}`}>
          <span class={`pulse-dot ${isFailover ? "red" : "green"}`} /> Serving DHCP
        </div>
      )}
      <div class="server-header">
        <div class={`server-icon ${role}`}>🖥</div>
        <div><div class="server-name">{name}</div><div class="server-host">{host}</div></div>
      </div>
      <span class={`server-role ${roleClass}`}>{isActive ? "Active" : role === "primary" ? "Primary" : "Standby"}</span>
      <div class="server-health">
        <span class={`dot ${health}`} />
        <span>{health === "healthy" ? "Healthy" : health === "unhealthy" ? "Unhealthy" : "Checking…"}</span>
      </div>
    </div>
  );
}

// -- Mount -------------------------------------------------------------------

const root = document.getElementById("app");
if (root) render(<FailoverPage />, root);

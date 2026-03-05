/** Type-safe API client for Tessera backend. */

const BASE = "/api/v1";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...opts?.headers },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(r.status, (body as { detail?: string }).detail ?? `Request failed (${r.status})`);
  }
  return r.json() as Promise<T>;
}

// -- Types -------------------------------------------------------------------

export interface VoterInfo {
  voter: string;
  status: string;
  timestamp: number;
  received_at: number;
  stale?: boolean;
}

export interface TransitionInfo {
  from_state: string;
  to_state: string;
  timestamp: number;
  reason: string;
}

export interface FailoverStatus {
  state: string;
  has_quorum: boolean;
  active_votes: number;
  up_count: number;
  down_count: number;
  consecutive_down: number;
  consecutive_up: number;
  voters: Record<string, VoterInfo>;
  transitions: TransitionInfo[];
  config: {
    quorum: number;
    failover_rounds: number;
    failback_rounds: number;
    vote_ttl: number;
  };
}

export interface ScopeListItem {
  name: string;
  enabled: boolean;
  start_address: string;
  end_address: string;
  subnet_mask: string;
}

export interface ScopesResponse {
  scopes: ScopeListItem[];
}

export interface ScopeDetailResponse {
  name: string;
  data: Record<string, unknown>;
}

export interface LeasesResponse {
  scope: string;
  leases: Record<string, unknown>[];
}

export interface BackupManifest {
  backup_id: string;
  created_at: number;
  source: string;
  description: string;
  scope_count: number;
  reservation_count: number;
}

export interface BackupListResponse {
  backups: BackupManifest[];
}

export interface RestoreResponse {
  backup_id: string;
  dry_run: boolean;
  changes: DriftChange[];
  total_changes: number;
}

export interface DriftChange {
  scope: string;
  action: string;
  detail: string;
}

export interface EnforcementStatus {
  mode: string;
  pinned_backup_id: string;
  check_interval: number;
  last_check: number;
  last_drift: number;
  drift_count: number;
  restore_count: number;
  backup_on_pin: boolean;
  auto_restore_cooldown: number;
  max_history: number;
  history: DriftEvent[];
}

export interface DriftEvent {
  detected_at: number;
  changes: DriftChange[];
  drift_summary: DriftChange[];
  change_count: number;
  action_taken: string;
  backup_id: string;
  count: number;
  last_seen: number;
}

export interface DriftCheckResponse {
  drift_detected: boolean;
  changes: DriftChange[];
  drift_summary: DriftChange[];
  total_changes: number;
  action: string;
}

export interface BackupSettings {
  auto_enabled: boolean;
  cron_schedule: string;
  max_backups: number;
  stored_backups: number;
  next_run: number;
  backup_dir: string;
}

// -- API functions -----------------------------------------------------------

export const api = {
  ping: () => request<{ status: string }>("/ping"),
  status: () => request<FailoverStatus>("/status"),

  // DHCP
  listScopes: () => request<ScopesResponse>("/scopes"),
  getScope: (name: string) => request<ScopeDetailResponse>(`/scopes/${enc(name)}`),
  createScope: (body: Record<string, unknown>) => request<{ message: string }>("/scopes", post(body)),
  updateScope: (name: string, settings: Record<string, unknown>) =>
    request<{ message: string }>(`/scopes/${enc(name)}`, { method: "PUT", body: JSON.stringify({ settings }) }),
  deleteScope: (name: string) => request<{ message: string }>(`/scopes/${enc(name)}`, { method: "DELETE" }),
  enableScope: (name: string) => request<{ message: string }>(`/scopes/${enc(name)}/enable`, { method: "POST" }),
  disableScope: (name: string) => request<{ message: string }>(`/scopes/${enc(name)}/disable`, { method: "POST" }),
  addReservation: (scope: string, body: Record<string, unknown>) =>
    request<{ message: string }>(`/scopes/${enc(scope)}/reservations`, post(body)),
  deleteReservation: (scope: string, mac: string) =>
    request<{ message: string }>(`/scopes/${enc(scope)}/reservations/${enc(mac)}`, { method: "DELETE" }),
  listLeases: (scope: string) => request<LeasesResponse>(`/leases/${enc(scope)}`),
  removeLease: (scope: string, address: string) =>
    request<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}`, { method: "DELETE" }),
  convertLease: (scope: string, address: string) =>
    request<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}/convert`, { method: "POST" }),

  // Backups
  listBackups: () => request<BackupListResponse>("/backups"),
  createBackup: (description = "") => request<BackupManifest>("/backups", post({ description })),
  deleteBackup: (id: string) => request<{ message: string }>(`/backups/${enc(id)}`, { method: "DELETE" }),
  restoreBackup: (id: string, dryRun: boolean) =>
    request<RestoreResponse>(`/backups/${enc(id)}/restore`, post({ dry_run: dryRun })),
  getBackupSettings: () => request<BackupSettings>("/backups/settings"),
  updateBackupSettings: (body: Partial<Pick<BackupSettings, "auto_enabled" | "cron_schedule" | "max_backups">>) =>
    request<BackupSettings>("/backups/settings", { method: "PUT", body: JSON.stringify(body) }),

  // Enforcement
  getEnforcement: () => request<EnforcementStatus>("/enforcement"),
  setEnforcementMode: (mode: string) => request<{ message: string }>("/enforcement/mode", post({ mode })),
  pinBackup: (backupId: string) => request<{ message: string }>("/enforcement/pin", post({ backup_id: backupId })),
  unpinBackup: () => request<{ message: string }>("/enforcement/unpin", { method: "POST" }),
  checkDrift: () => request<DriftCheckResponse>("/enforcement/check", { method: "POST" }),
  updateEnforcementSettings: (body: Record<string, unknown>) =>
    request<{ message: string }>("/enforcement/settings", { method: "PUT", body: JSON.stringify(body) }),
  acceptDrift: () => request<{ new_backup_id: string; message: string }>("/enforcement/accept", { method: "POST" }),
} as const;

function enc(s: string): string {
  return encodeURIComponent(s);
}

function post(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

export { ApiError };

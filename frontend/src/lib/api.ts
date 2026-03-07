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

export interface PaginationMeta {
  total: number;
  offset: number;
  limit: number;
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
  transitions_pagination: PaginationMeta;
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
  pagination: PaginationMeta;
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
  pagination: PaginationMeta;
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
  history_pagination: PaginationMeta;
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

// -- Server types ------------------------------------------------------------

export interface ServerInfo {
  name: string;
  url: string;
  role: string;
  priority: number;
  status: string;
  message: string;
}

export interface ServersResponse {
  servers: ServerInfo[];
}

export interface PromoteDemoteResponse {
  name: string;
  new_role: string;
  message: string;
}

// -- Voter types -------------------------------------------------------------

export interface VoterInfoDetail {
  name: string;
  registered_at: number;
  approved_at: number | null;
  status: string;
  last_vote: number;
  ip_address: string;
}

export interface VoterListResponse {
  voters: VoterInfoDetail[];
}

export interface VoterApproveResponse {
  voter_name: string;
  psk: string;
  status: string;
}

export interface VoterRevokeResponse {
  voter_name: string;
  status: string;
}

export interface RegistrationTokenResponse {
  token: string;
  created_at: number;
  expires_at: number;
  bind_ip: string | null;
}

export interface RegistrationTokenInfo {
  token: string;
  created_at: number;
  expires_at: number;
  used: boolean;
  used_by: string | null;
  bind_ip: string | null;
}

export interface RegistrationTokenListResponse {
  tokens: RegistrationTokenInfo[];
}

export interface KeyRotateResponse {
  voter_name: string;
  new_psk: string;
  grace_period: number;
  message: string;
}

// -- API functions -----------------------------------------------------------

export const api = {
  ping: () => request<{ status: string }>("/ping"),
  status: (transitionsOffset = 0, transitionsLimit = 20) =>
    request<FailoverStatus>(`/status?transitions_offset=${transitionsOffset}&transitions_limit=${transitionsLimit}`),

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
  updateReservation: (scope: string, mac: string, body: Record<string, unknown>) =>
    request<{ message: string }>(`/scopes/${enc(scope)}/reservations/${enc(mac)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteReservation: (scope: string, mac: string) =>
    request<{ message: string }>(`/scopes/${enc(scope)}/reservations/${enc(mac)}`, { method: "DELETE" }),
  listLeases: (scope: string, offset = 0, limit = 50) =>
    request<LeasesResponse>(`/leases/${enc(scope)}?offset=${offset}&limit=${limit}`),
  removeLease: (scope: string, address: string) =>
    request<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}`, { method: "DELETE" }),
  convertLease: (scope: string, address: string) =>
    request<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}/convert`, { method: "POST" }),

  // Backups
  listBackups: (offset = 0, limit = 20) =>
    request<BackupListResponse>(`/backups?offset=${offset}&limit=${limit}`),
  createBackup: (description = "") => request<BackupManifest>("/backups", post({ description })),
  deleteBackup: (id: string) => request<{ message: string }>(`/backups/${enc(id)}`, { method: "DELETE" }),
  restoreBackup: (id: string, dryRun: boolean) =>
    request<RestoreResponse>(`/backups/${enc(id)}/restore`, post({ dry_run: dryRun })),
  getBackupSettings: () => request<BackupSettings>("/backups/settings"),
  updateBackupSettings: (body: Partial<Pick<BackupSettings, "auto_enabled" | "cron_schedule" | "max_backups">>) =>
    request<BackupSettings>("/backups/settings", { method: "PUT", body: JSON.stringify(body) }),

  // Enforcement
  getEnforcement: (historyOffset = 0, historyLimit = 20) =>
    request<EnforcementStatus>(`/enforcement?history_offset=${historyOffset}&history_limit=${historyLimit}`),
  setEnforcementMode: (mode: string) => request<{ message: string }>("/enforcement/mode", post({ mode })),
  pinBackup: (backupId: string) => request<{ message: string }>("/enforcement/pin", post({ backup_id: backupId })),
  unpinBackup: () => request<{ message: string }>("/enforcement/unpin", { method: "POST" }),
  checkDrift: () => request<DriftCheckResponse>("/enforcement/check", { method: "POST" }),
  updateEnforcementSettings: (body: Record<string, unknown>) =>
    request<{ message: string }>("/enforcement/settings", { method: "PUT", body: JSON.stringify(body) }),
  acceptDrift: () => request<{ new_backup_id: string; message: string }>("/enforcement/accept", { method: "POST" }),

  // Servers
  listServers: () => request<ServersResponse>("/servers"),
  promoteServer: (name: string) => request<PromoteDemoteResponse>(`/servers/${enc(name)}/promote`, { method: "POST" }),
  demoteServer: (name: string) => request<PromoteDemoteResponse>(`/servers/${enc(name)}/demote`, { method: "POST" }),

  // Voters
  listVoters: () => request<VoterListResponse>("/voters"),
  listPendingVoters: () => request<VoterListResponse>("/voters/pending"),
  generateToken: (opts?: { bind_ip?: string; ttl?: number }) => request<RegistrationTokenResponse>("/voters/tokens", post(opts ?? {})),
  listTokens: () => request<RegistrationTokenListResponse>("/voters/tokens"),
  approveVoter: (name: string) => request<VoterApproveResponse>(`/voters/${enc(name)}/approve`, { method: "POST" }),
  revokeVoter: (name: string) => request<VoterRevokeResponse>(`/voters/${enc(name)}/revoke`, { method: "POST" }),
  deleteVoter: (name: string) => request<VoterRevokeResponse>(`/voters/${enc(name)}`, { method: "DELETE" }),
  rotateVoterKey: (name: string) => request<KeyRotateResponse>(`/voters/${enc(name)}/rotate-key`, { method: "POST" }),
} as const;

function enc(s: string): string {
  return encodeURIComponent(s);
}

function post(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

export { ApiError };

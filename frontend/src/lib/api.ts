/** Type-safe API client for Tessera backend. */

import { signal } from "@preact/signals";

const BASE = "/api/v1";

// -- Admin auth state --------------------------------------------------------

/** Stored admin API key (survives page reloads within the session). */
export const adminToken = signal<string>(sessionStorage.getItem("tessera_admin_token") ?? "");

export function setAdminToken(token: string): void {
  adminToken.value = token;
  if (token) sessionStorage.setItem("tessera_admin_token", token);
  else sessionStorage.removeItem("tessera_admin_token");
}

export function clearAdminToken(): void {
  setAdminToken("");
}

/** Signal that triggers the auth dialog. Resolves when user enters token. */
export const authPromptOpen = signal(false);

let _authResolve: (() => void) | null = null;
let _authReject: ((err: Error) => void) | null = null;

/** Request admin auth — opens dialog, returns a promise that resolves when token is set. */
export function requestAuth(): Promise<void> {
  if (adminToken.value) return Promise.resolve();
  authPromptOpen.value = true;
  return new Promise<void>((resolve, reject) => { _authResolve = resolve; _authReject = reject; });
}

/** Called by the auth dialog after successful verification. */
export function resolveAuth(): void {
  authPromptOpen.value = false;
  _authResolve?.();
  _authResolve = null;
  _authReject = null;
}

export function cancelAuth(): void {
  authPromptOpen.value = false;
  _authReject?.(new ApiError(0, "Authentication cancelled"));
  _authResolve = null;
  _authReject = null;
}

export function isAuthCancelled(e: unknown): boolean {
  return e instanceof ApiError && e.status === 0;
}

// -- Request helper ----------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): Record<string, string> {
  const tok = adminToken.value;
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...opts?.headers },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(r.status, (body as { detail?: string }).detail ?? `Request failed (${r.status})`);
  }
  return r.json() as Promise<T>;
}

/** Make an admin request — prompts for token if not set or on 401/503. */
async function adminRequest<T>(path: string, opts?: RequestInit): Promise<T> {
  // If no token, prompt first
  if (!adminToken.value) await requestAuth();
  if (!adminToken.value) throw new ApiError(401, "Authentication required");

  try {
    return await request<T>(path, opts);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 503)) {
      // Token invalid or not configured — clear and re-prompt
      clearAdminToken();
      await requestAuth();
      if (!adminToken.value) throw new ApiError(401, "Authentication required");
      return request<T>(path, opts);
    }
    throw e;
  }
}

// -- Types -------------------------------------------------------------------

export interface VoterInfo {
  voter: string;
  status: string;
  timestamp: number;
  received_at: number;
  stale?: boolean;
  verification?: string;  // "verified", "unverified", "failed"
  http_status?: string;   // "up", "down", or absent
  dhcp_status?: string;   // "up", "down", or absent
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

export interface AddServerRequest {
  name: string;
  url: string;
  role?: string;
  priority?: number;
  token?: string;
}

export interface AddServerResponse {
  name: string;
  role: string;
  message: string;
}

export interface RemoveServerResponse {
  name: string;
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
  bind_ip: string;
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
  verifyToken: () => adminRequest<{ status: string }>("/auth/verify", { method: "POST" }),

  // DHCP (reads are public, writes are admin)
  listScopes: () => request<ScopesResponse>("/scopes"),
  getScope: (name: string) => request<ScopeDetailResponse>(`/scopes/${enc(name)}`),
  createScope: (body: Record<string, unknown>) => adminRequest<{ message: string }>("/scopes", post(body)),
  updateScope: (name: string, settings: Record<string, unknown>) =>
    adminRequest<{ message: string }>(`/scopes/${enc(name)}`, { method: "PUT", body: JSON.stringify({ settings }) }),
  deleteScope: (name: string) => adminRequest<{ message: string }>(`/scopes/${enc(name)}`, { method: "DELETE" }),
  enableScope: (name: string) => adminRequest<{ message: string }>(`/scopes/${enc(name)}/enable`, { method: "POST" }),
  disableScope: (name: string) => adminRequest<{ message: string }>(`/scopes/${enc(name)}/disable`, { method: "POST" }),
  addReservation: (scope: string, body: Record<string, unknown>) =>
    adminRequest<{ message: string }>(`/scopes/${enc(scope)}/reservations`, post(body)),
  updateReservation: (scope: string, mac: string, body: Record<string, unknown>) =>
    adminRequest<{ message: string }>(`/scopes/${enc(scope)}/reservations/${enc(mac)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteReservation: (scope: string, mac: string) =>
    adminRequest<{ message: string }>(`/scopes/${enc(scope)}/reservations/${enc(mac)}`, { method: "DELETE" }),
  listLeases: (scope: string, offset = 0, limit = 50) =>
    request<LeasesResponse>(`/leases/${enc(scope)}?offset=${offset}&limit=${limit}`),
  removeLease: (scope: string, address: string) =>
    adminRequest<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}`, { method: "DELETE" }),
  convertLease: (scope: string, address: string) =>
    adminRequest<{ message: string }>(`/leases/${enc(scope)}/${enc(address)}/convert`, { method: "POST" }),

  // Backups
  listBackups: (offset = 0, limit = 20) =>
    request<BackupListResponse>(`/backups?offset=${offset}&limit=${limit}`),
  createBackup: (description = "") => adminRequest<BackupManifest>("/backups", post({ description })),
  deleteBackup: (id: string) => adminRequest<{ message: string }>(`/backups/${enc(id)}`, { method: "DELETE" }),
  restoreBackup: (id: string, dryRun: boolean) =>
    adminRequest<RestoreResponse>(`/backups/${enc(id)}/restore`, post({ dry_run: dryRun })),
  getBackupSettings: () => request<BackupSettings>("/backups/settings"),
  updateBackupSettings: (body: Partial<Pick<BackupSettings, "auto_enabled" | "cron_schedule" | "max_backups">>) =>
    adminRequest<BackupSettings>("/backups/settings", { method: "PUT", body: JSON.stringify(body) }),

  // Enforcement
  getEnforcement: (historyOffset = 0, historyLimit = 20) =>
    request<EnforcementStatus>(`/enforcement?history_offset=${historyOffset}&history_limit=${historyLimit}`),
  setEnforcementMode: (mode: string) => adminRequest<{ message: string }>("/enforcement/mode", post({ mode })),
  pinBackup: (backupId: string) => adminRequest<{ message: string }>("/enforcement/pin", post({ backup_id: backupId })),
  unpinBackup: () => adminRequest<{ message: string }>("/enforcement/unpin", { method: "POST" }),
  checkDrift: () => adminRequest<DriftCheckResponse>("/enforcement/check", { method: "POST" }),
  updateEnforcementSettings: (body: Record<string, unknown>) =>
    adminRequest<{ message: string }>("/enforcement/settings", { method: "PUT", body: JSON.stringify(body) }),
  acceptDrift: () => adminRequest<{ new_backup_id: string; message: string }>("/enforcement/accept", { method: "POST" }),

  // Servers
  listServers: () => request<ServersResponse>("/servers"),
  promoteServer: (name: string) => adminRequest<PromoteDemoteResponse>(`/servers/${enc(name)}/promote`, { method: "POST" }),
  demoteServer: (name: string) => adminRequest<PromoteDemoteResponse>(`/servers/${enc(name)}/demote`, { method: "POST" }),
  addServer: (body: AddServerRequest) => adminRequest<AddServerResponse>("/servers", post(body)),
  removeServer: (name: string) => adminRequest<RemoveServerResponse>(`/servers/${enc(name)}`, { method: "DELETE" }),

  // Voters
  listVoters: () => request<VoterListResponse>("/voters"),
  listPendingVoters: () => request<VoterListResponse>("/voters/pending"),
  generateToken: (opts?: { bind_ip?: string; ttl?: number }) => adminRequest<RegistrationTokenResponse>("/voters/tokens", post(opts ?? {})),
  listTokens: () => request<RegistrationTokenListResponse>("/voters/tokens"),
  approveVoter: (name: string) => adminRequest<VoterApproveResponse>(`/voters/${enc(name)}/approve`, { method: "POST" }),
  revokeVoter: (name: string) => adminRequest<VoterRevokeResponse>(`/voters/${enc(name)}/revoke`, { method: "POST" }),
  deleteVoter: (name: string) => adminRequest<VoterRevokeResponse>(`/voters/${enc(name)}`, { method: "DELETE" }),
  rotateVoterKey: (name: string) => adminRequest<KeyRotateResponse>(`/voters/${enc(name)}/rotate-key`, { method: "POST" }),
  deleteToken: (prefix: string) => adminRequest<{ message: string }>(`/voters/tokens/${enc(prefix)}`, { method: "DELETE" }),
} as const;

function enc(s: string): string {
  return encodeURIComponent(s);
}

function post(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}


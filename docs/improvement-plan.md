# Tessera Improvement Plan

Prioritised by impact. Each item tagged: **[security]**, **[resilience]**, **[stability]**, **[operability]**.

---

## P0 — Do Now

### 1. Replay protection on vote signatures **[security]**

**Problem:** Vote HMAC is `VOTER_NAME|STATUS|TIMESTAMP` — an attacker who captures one signed vote can replay it within the rate-limit window since there's no nonce. Worse: if timestamps are coarse (second-level), a captured vote with an identical timestamp passes signature verification indefinitely.

**Fix:**
- Add a per-voter monotonic sequence number to the signed payload: `VOTER_NAME|STATUS|TIMESTAMP|SEQ`
- Server tracks last accepted `seq` per voter, rejects `seq <= last_seen`
- Voter script reads last seq from a local file, increments on each run

**Effort:** ~2h backend + voter script

### 2. Secrets in query parameters **[security]**

**Problem:** Every Technitium API call passes `token=<api_key>` as a URL query parameter (`_request()` in technitium.py). Query params end up in:
- Technitium access logs
- Reverse proxy logs (Traefik)
- Potentially browser history if any debug URLs are accessed

**Fix:**
- Check if Technitium supports `Authorization` header auth — if yes, switch
- If not, document the risk and ensure log redaction in Traefik (`accessLog.fields.headers.names`)

**Effort:** ~1h

### 3. File write atomicity gaps **[stability]**

**Problem:** `voter_registry.py` does `write_text(json.dumps(...))` directly for voter keys and registry files. If the process crashes mid-write or disk fills, you get a corrupted/truncated JSON file → startup failure → no voter auth → failover blind.

`settings_store.py` already does atomic writes (tmp + rename). Other files don't.

**Fix:**
- Extract the atomic write pattern from `SettingsStore` into a shared `atomic_write(path, content)` utility
- Use it everywhere: voter registry, voter keys, servers.json updates in technitium pool, backup manifests

**Effort:** ~1h

### 4. Backup integrity validation on restore **[resilience]**

**Problem:** `backup.py` computes SHA-256 checksums on create but `_validate_backup_integrity()` only verifies the stored checksum field exists and matches a recomputed hash of the JSON body. It doesn't validate that the scope data is structurally valid before attempting a restore — a corrupt backup could leave scopes in a partial state.

**Fix:**
- Add schema validation (scope names, required fields, reservation format) before restore
- Dry-run restore: diff what would change before applying
- If any scope restore fails, halt and log which scopes succeeded (for manual cleanup)

**Effort:** ~3h

---

## P1 — Do Soon

### 5. TLS verification defaults **[security]**

**Problem:** `TechnitiumClient` defaults to `verify=False` and warns once per URL. In production with `TESSERA_CA_CERT_FILE` set this is fine, but the default is insecure and the warning is easily lost in log noise.

**Fix:**
- Make `verify=True` the default
- If a user explicitly sets `TESSERA_SKIP_TLS_VERIFY=true`, disable it with a startup WARNING (not just per-request)
- Log level ERROR (not WARNING) if TLS is disabled and no CA cert is set

**Effort:** ~30min

### 6. Failover state persistence **[resilience]**

**Problem:** If Tessera restarts while in `ACTIVE` failover state, it boots into `STANDBY` — meaning candidate DHCP scopes get disabled even though the primary might still be down. There's a window where no DHCP server is active.

**Fix:**
- Persist failover state to disk (atomic write) on every state transition
- On startup, load last state; if `ACTIVE`, skip the initial `failover_rounds` wait and keep candidate scopes enabled
- Add a startup log line: "Resuming failover state: ACTIVE" or "Starting fresh: STANDBY"

**Effort:** ~2h

### 7. Rate limiting on admin endpoints **[security]**

**Problem:** `POST /api/v1/auth/verify` has no rate limiting. An attacker behind O2P can brute-force the admin API key. The voter vote endpoint has per-voter rate limiting, but admin auth doesn't.

**Fix:**
- Add IP-based rate limiting on `/auth/verify` (e.g., 5 attempts per minute per IP)
- After N failures, exponential backoff or temporary lockout
- Log failed attempts at WARNING level with source IP

**Effort:** ~1h

### 8. Health check endpoint depth **[resilience]**

**Problem:** `/health` returns engine statuses but doesn't probe actual dependencies. An engine can report `RUNNING` while Technitium is unreachable.

**Fix:**
- Add `/health?deep=true` that actually pings Technitium (cached for 10s), checks file permissions on config/data dirs, verifies voter keys file is parseable
- Return `503` if any critical dependency is down
- Default `/health` stays shallow (for load balancer probes)

**Effort:** ~2h

### 9. Config watcher error handling **[stability]**

**Problem:** `config_watcher.py` has `except Exception` on every file read. If `servers.json` becomes unparseable (bad edit, truncated write), the watcher silently ignores it and the pool keeps the old state. No alert, no metric, no log at ERROR level — just a generic catch.

**Fix:**
- Log at ERROR with the specific parse error and file path
- Add a `config_parse_errors` counter metric
- After N consecutive parse failures, set engine health to DEGRADED
- Emit a structured event that could trigger an alert

**Effort:** ~1h

### 10. Scope sync conflict resolution **[resilience]**

**Problem:** Scope sync diffs reservations by MAC address and adds/removes to match. If the active server has a reservation change mid-sync, the sync could remove a just-added reservation from the candidate. There's no locking or version check.

**Fix:**
- Add a "last-seen" timestamp to each sync cycle
- Compare reservation sets atomically (snapshot both servers, then diff)
- If active server state changed during sync (mtime or revision check), retry instead of applying partial diff
- Log sync conflicts at WARNING

**Effort:** ~3h

---

## P2 — Do When Time Allows

### 11. Structured metrics endpoint **[operability]**

**Problem:** `get_metrics()` exists on engines but there's no `/metrics` endpoint. Monitoring is dashboard-only.

**Fix:**
- Add `/metrics` endpoint (Prometheus format or JSON)
- Key metrics: votes per voter, quorum state, failover transitions count, scope sync duration, backup age, enforcement drift count, config reload count
- No dependency on prometheus-client lib — just text format output

**Effort:** ~3h

### 12. Graceful degradation on voter loss **[resilience]**

**Problem:** If voters drop below quorum (e.g., 3 of 5 servers go down), no votes pass quorum → no failover can trigger → DHCP stays on potentially-dead primary. The system is designed to require quorum, but there's no alert when quorum is unreachable.

**Fix:**
- Track "last quorum reachable" timestamp
- If no quorum for `2 × vote_expiry`, emit WARNING log: "Quorum unreachable — failover disabled"
- Expose this state in the dashboard (e.g., "⚠️ Insufficient voters for quorum")
- Consider a configurable "emergency mode" that reduces quorum threshold after sustained loss

**Effort:** ~2h

### 13. Test coverage gaps **[stability]**

**Current:** 78% overall. Key gaps:
- `config_watcher.py`: 33% (file watching, reload logic, SIGHUP handler)
- `pages.py`: 45% (template rendering, asset map building)
- `registry.py`: 52% (dependency-ordered start/stop, health aggregation)

**Fix:**
- Config watcher: mock filesystem with tmp files, test reload triggers
- Pages: test asset regex against known Vite output patterns
- Registry: test dependency ordering, circular dependency detection, partial startup failure
- Target: 90%+

**Effort:** ~4h

### 14. Audit trail for admin actions **[security]**

**Problem:** Admin actions (generate token, approve voter, revoke voter, add/remove server, trigger backup, change enforcement mode) are not logged in a queryable audit trail. `journalctl` has the info but it's scattered across log lines.

**Fix:**
- Add an `AuditEvent` dataclass with: timestamp, action, actor (IP), target, detail
- Write to a dedicated audit log file (JSON lines) alongside normal logging
- Add `GET /api/v1/audit` endpoint (admin-only, paginated)
- Retention: configurable, default 90 days

**Effort:** ~4h

### 15. Voter certificate auth (optional, future) **[security]**

**Problem:** Voter authentication is HMAC-PSK — if a PSK leaks, an attacker can impersonate that voter until key rotation. PSKs are stored in plaintext on voter machines.

**Fix (future):**
- Support mTLS for voter endpoints using Dogtag-issued client certs
- Each voter gets a short-lived cert (auto-renewed via ACME or EST)
- Falls back to HMAC-PSK for environments without PKI
- Significantly raises the bar: attacker needs the private key, not just a hex string

**Effort:** ~8h (significant, defer until PKI is mature)

---

## P3 — Nice to Have

### 16. Canary/synthetic voter **[operability]**

Run a synthetic voter inside the Tessera container itself that probes both DHCP servers and submits internal-only votes. Gives you a baseline even if all external voters are misconfigured.

### 17. Backup encryption at rest **[security]**

Backups contain full DHCP config including reserved IPs and MACs. Encrypt with a key derived from `TESSERA_ADMIN_API_KEY` or a dedicated `TESSERA_BACKUP_ENCRYPTION_KEY`. Use Fernet (symmetric, authenticated).

### 18. Webhook/notification on state transitions **[operability]**

Fire a webhook (or send to a configurable URL) on: failover trigger, failback, voter registration, drift detected, backup failure. Enables integration with alerting (Gotify, ntfy, Discord, etc.) without polling.

### 19. Read-only API mode **[resilience]**

`TESSERA_READ_ONLY=true` disables all write endpoints. Useful for secondary/observer instances that only display status.

---

## Implementation Order

```
Sprint 1 (security-critical):  #1 replay protection, #2 query param secrets, #7 admin rate limit
Sprint 2 (data integrity):     #3 atomic writes, #4 backup validation, #6 failover persistence
Sprint 3 (observability):      #5 TLS defaults, #8 deep health, #9 config watcher, #11 metrics
Sprint 4 (resilience):         #10 sync conflicts, #12 quorum alerts, #13 test coverage
Sprint 5 (audit/hardening):    #14 audit trail, #17 backup encryption, #18 webhooks
```

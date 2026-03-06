# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - Unreleased

### Added

- Initial Tessera DHCP management platform with FastAPI backend
- Voter quorum failover engine with HMAC-SHA256 signed votes
- Scope sync engine — periodic reservation sync from primary to standby
- Backup engine — scheduled DHCP config snapshots with cron and retention policy
- Enforcement engine — drift detection and automatic rollback
- Multi-server pool (`TechnitiumPool`) with runtime promotion/demotion
- Vite + Preact island architecture for the web dashboard
- Modal dialogs, toast notifications, confirm dialogs, keyboard navigation
- DHCP proxy API — typed REST endpoints for scopes, leases, and reservations
- Pagination for backups, leases, transitions, and drift history
- DHCP verification: voter probes + server-side vote cross-validation
- Authenticated voter API with per-voter rate limiting
- Voter self-registration with auto-approve and PSK grace period
- Config watcher engine — live reload of voter keys, server list, and tokens
- Automated voter installer script with full edge-case handling
- Structured JSON logging with request-ID propagation
- Systemd service and timer units
- Docker multi-stage build with compose and secrets support
- `response_model` on all API endpoint decorators
- OpenAPI tag descriptions for all router groups
- `X-API-Version: v1` response header on all requests
- `TESSERA_CORS_ORIGINS` setting for optional CORS middleware
- `TESSERA_CA_CERT_FILE` setting for custom CA trust in httpx clients
- Asset map cache with TTL (5 min) instead of unbounded `lru_cache`
- GPLv3 license
- `THIRD_PARTY_NOTICES.md` with all dependency credits
- Comprehensive installation, architecture, and development documentation

### Changed

- Rewritten architecture and deployment docs
- Enforce mode auto-restore indicators; hide manual restore buttons when active

### Fixed

- Bare `RuntimeError` and `assert isinstance` in dependency injection
- Scope sync standby client start, `ipAddress` field, logging config
- Lease filtering by scope IP range (Technitium ignores name param)
- Multi-stage Dockerfile and compose secrets mounts

### Security

- Security headers on all responses (CSP, X-Frame-Options, etc.)
- HMAC-SHA256 vote authentication with replay protection
- Rate limiting on voter endpoints

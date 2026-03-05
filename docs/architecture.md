# Architecture

See `src/tessera/` for the source tree. Core components:

- **EngineRegistry** — lifecycle-managed business logic units
- **TechnitiumClient** — async HTTP client for Technitium DHCP API
- **FailoverEngine** — voter quorum state machine
- **ScopeSyncEngine** — periodic reservation sync
- **FastAPI routes** — `/api/v1/` prefix, Pydantic response models
- **Vue 3 SPA** — served from `/`, CDN-loaded Vue

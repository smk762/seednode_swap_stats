## Complete initial implementation: API, tests, and docs

Reference: [PR #2: Complete initial implementation, adds tests and docs](https://github.com/smk762/seednode_swap_stats/pull/2)

### Summary
This PR delivers the first complete, runnable version of Seednode Swap Stats. It includes:
- FastAPI backend that reads the KDF SQLite DB and exposes event- and trader-focused endpoints
- Dockerized setup with `kdf` and `api` services wired via `docker compose`
- Initial events configuration (`events.json`) and an example competition (KMD/ARRR/DGB, Nov 2025)
- Pubkey hashing for privacy, configurable via `PUBKEY_HASH_KEY`
- Optional historical backfill at startup
- Unit tests for core logic and an easy test runner service
- Documentation updates (README, production setup, testing)

### Key changes
- API (FastAPI)
  - Grouped events and details
    - `GET /events?filter=complete|active|upcoming`
    - `GET /event_details?event_name=GROUP`
  - Trader stats by event group(s)
    - `GET /traders?event_name=GROUP[,GROUP2]&limit=&offset=&search=&verbose=`
    - `GET /trader_swaps?event_name=GROUP[,GROUP2]&pubkey=&limit=&offset=&search=`
  - Swap inspection and identification
    - `GET /swap/{uuid}` — returns swap with pubkeys removed and HMAC/SHA hashes added
    - `GET /identify?uuid=&ticker=` — returns hashed pubkey for one side of a swap by ticker
    - `GET /hash_pubkey?pubkey=` — hash helper (HMAC with `PUBKEY_HASH_KEY`, fallback to SHA-256)
  - Registrations
    - `GET /players` — moniker -> pubkey_hash for registered players
    - `POST /register` — begin registration flow (generates randomized DOC fee in configured range)
  - Health
    - `GET /healthz`
  - Notes:
    - Pubkeys are never returned in plaintext from public endpoints; hashes are provided instead.
    - Some legacy endpoints/websocket behavior were removed/omitted in favor of event-focused APIs.

- Configuration
  - Centralized via `api/app/config.py` (pydantic `BaseSettings`)
  - Major env vars:
    - `KDF_DB_PATH` — path to KDF MM2.db inside container (compose mounts `./.kdf` accordingly)
    - `EVENTS_JSON_PATH` — path to events JSON (defaults to `events.json`)
    - `PUBKEY_HASH_KEY` — HMAC key for pubkey hashing (change in production)
    - `KDF_LOAD_HISTORY`, `RETENTION_HOURS`, `BACKFILL_SINCE`
    - DOC Insight + registration parameters (`DOC_INSIGHT_BASE_URL`, `DOC_INSIGHT_API_PATH`, `REGISTRATION_*`)
  - `.env` supported; also `ENV_FILE` override

- Docker & Compose
  - `kdf` service (from `Dockerfile.kdf`) runs Komodo DeFi Framework; persists to `./.kdf`
  - `api` service (from `api/Dockerfile`) runs FastAPI; has hot reload for development
  - Ports:
    - API: `8000`
    - KDF: `8870` (RPC), `42845` and `42855` (p2p)
  - Volumes:
    - `./.kdf:/home/komodian/.kdf` (RO for `api`, RW for `kdf`)
    - `./api/app:/app/app` (RW for hot reload and persistent `DEX_COMP.db` registration store)
    - `./events.json:/app/events.json:ro`
    - `./.env:/app/.env:ro` (optional)

- Events
  - Adds example KMD/ARRR/DGB event group (Nov 2025) in `events.json`

- Tests
  - `api/tests/` with pytest
  - `api_tests` compose service to run tests in an isolated container

- Documentation
  - Updated `README.md` with quick start, endpoint overview, config reference
  - Added `docs/PRODUCTION_SETUP.md` for new-server deployment steps
  - `api/TESTING.md` for test workflows

### How to run
```bash
# Build and start
docker compose up -d --build

# Verify API health
curl http://localhost:8000/healthz

# Follow logs
docker compose logs -f api
docker compose logs -f kdf
```

### Configuration (high level)
- Create `MM2.json` from `MM2.json.template` and set a strong `"rpc_password"` before running KDF.
- Optional `.env` in repo root for API overrides, e.g.:
  - `PUBKEY_HASH_KEY` (required to change in production)
  - `BACKFILL_SINCE`, `RETENTION_HOURS`, `EVENTS_JSON_PATH`, `REGISTRATION_*`

### Security notes
- Use a strong RPC password in `MM2.json`.
- Avoid exposing KDF RPC and P2P ports publicly unless required.
- Run API behind a TLS-terminating reverse proxy in production.

### Breaking/behavioral changes
- Legacy endpoints/websocket were removed/omitted in favor of event-focused routes and hashing.

### Verification checklist
- API returns 200 on `/healthz`
- `/events` and `/event_details` return data from `events.json`
- `/traders` and `/trader_swaps` return data derived from KDF DB with USD values
- `/swap/{uuid}` returns hashed pubkeys; `/identify` returns HMAC/SHA hash as expected
- Tests pass via `docker compose run --rm api_tests`

### Follow-ups
- Expand endpoint docs with examples and schemas
- Provide `.env.example`
- Optional: Nginx/Caddy reverse-proxy snippets with TLS automation



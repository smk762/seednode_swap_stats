Seednode Swap Stats

Overview
- Tracks and serves swap statistics from a Komodo DeFi Framework (KDF) node using a FastAPI backend.
- Dockerized with two services:
  - kdf: runs the Komodo DeFi Framework and persists its data under ./.kdf
  - api: FastAPI app that reads the KDF SQLite DB and exposes endpoints for events, traders, and registrations

Repository layout
- docker-compose.yml: Orchestrates kdf and api services
- Dockerfile.kdf: Image for the KDF service
- api/Dockerfile: Image for the FastAPI service
- api/app/: FastAPI source code
- api/tests/: Unit tests for API logic
- MM2.json.template: Template KDF configuration
- events.json: Sample event groups and windows
- .kdf/: KDF data directory (created at runtime; mounted into kdf and api)

Prerequisites
- Docker Engine and the Docker Compose plugin
- Git

Quick start
1) Configure KDF
   - Copy MM2.json.template to MM2.json
   - Set a strong rpc_password inside MM2.json

2) Optional: Environment overrides
   - Create a .env file in the repository root to override defaults. Common keys:
     - KDF_DB_PATH: Path inside the kdf data dir to the MM2.db (default points to ./.kdf/DB/.../MM2.db)
     - EVENTS_JSON_PATH: Path to events.json (default: events.json)
     - PUBKEY_HASH_KEY: Secret used to HMAC-hash pubkeys before returning them
     - REGISTRATION_DOC_ADDRESS: DOC address to receive registration fees
     - RETENTION_HOURS: Hours to retain non-event data in memory (default: 1)
     - BACKFILL_SINCE: Unix timestamp to backfill swaps from on startup

3) Launch services

```bash
docker compose up -d --build
```

4) Verify

```bash
curl http://localhost:8000/healthz
```

Useful commands
- See logs:
  - KDF: docker compose logs -f kdf
  - API: docker compose logs -f api
- Rebuild after code/config updates:

```bash
docker compose up -d --build
```

Configuration reference (API)
- The API reads configuration from environment variables; defaults are defined in api/app/config.py.
  - KDF_DB_PATH: Absolute path to the KDF SQLite DB inside the container (default: /home/komodian/.kdf/DB/.../MM2.db). In compose, ./.kdf is mounted to that location.
  - EVENTS_JSON_PATH: Path to events.json (default: events.json). In compose, ./events.json is mounted at /app/events.json.
  - KDF_LOAD_HISTORY: Load historical swaps at startup (default: True)
  - RETENTION_HOURS: Hours to retain non-event data in memory (default: 1)
  - BACKFILL_SINCE: Unix timestamp to backfill swaps from (default: unset)
  - PUBKEY_HASH_KEY: Secret key for hashing pubkeys (default: komodian). Change for production.
  - DOC_INSIGHT_BASE_URL: Insight base URL for DOC (default: https://doc.explorer.dexstats.info)
  - DOC_INSIGHT_API_PATH: Insight API path (default: insight-api-komodo)
  - REGISTRATION_DOC_ADDRESS: Destination DOC address for registrations (default set in code)
  - REGISTRATION_POLL_SECONDS: Interval to poll chain for registration payments (default: 180)
  - REGISTRATION_EXPIRY_HOURS: Pending registration expiry (default: 24)
  - REGISTRATION_AMOUNT_MIN / REGISTRATION_AMOUNT_MAX: Randomized fee range (default: 0.001 / 3.33)
  - REGISTRATION_DB_PATH: Path to the lightweight registration DB (default: /app/app/DEX_COMP.db). In compose, api/app is mounted, so this persists on the host at api/app/DEX_COMP.db.

API endpoints (non-exhaustive)
- GET /healthz: Liveness probe
- GET /events?filter=complete|active|upcoming: List grouped event names
- GET /event_details?event_name=GROUP: Group details and window
- GET /traders?event_name=GROUP[,GROUP2]&limit=&offset=&search=&verbose=: Ranked traders for event group(s)
- GET /trader_swaps?event_name=GROUP[,GROUP2]&pubkey=&limit=&offset=&search=: Swaps for a trader within event group(s)
- GET /swap/{uuid}: Swap details with pubkeys hashed
- GET /identify?uuid=&ticker=: HMAC hash for a specific side of a swap
- GET /hash_pubkey?pubkey=: HMAC or SHA-256 hash for a raw pubkey
- GET /players: Registered players (moniker -> pubkey_hash)

Development
- Hot reload is enabled for the api service (uvicorn --reload); the api/app directory is mounted read-write.
- Run tests (see api/TESTING.md for details):

```bash
docker compose run --rm api_tests
```

Data and persistence
- ./.kdf is mounted into kdf at /home/komodian/.kdf and into api as read-only.
- api/app/DEX_COMP.db persists on the host via the api/app bind mount.

Security notes
- Always set a strong rpc_password in MM2.json before exposing KDF beyond localhost.
- Do not expose KDF RPC and p2p ports publicly unless required; keep the API behind a reverse proxy with HTTPS in production.





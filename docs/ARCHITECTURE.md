# Architecture Documentation

## System Overview

The Alpaca Paper Trading Bot is a Dockerized, self-hosted paper-trading application that connects exclusively to Alpaca's paper trading API. It consists of three main services:

1. **Frontend** (Nginx + React SPA) - Port 80
2. **Backend** (FastAPI + Uvicorn) - Port 8000
3. **Worker** (APScheduler) - Background process

All services share a SQLite database via a named Docker volume (`bot_data`).

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Network                            │
├─────────────────┬─────────────────┬─────────────────────────────┤
│    Frontend     │    Backend      │          Worker             │
│  (React SPA)    │   (FastAPI)     │    (APScheduler)            │
│   Port 80       │   Port 8000     │    (No external port)       │
├─────────────────┼─────────────────┼─────────────────────────────┤
│                 │                 │                             │
│  - Setup Page   │  - Auth API     │  - Scheduler                │
│  - Strategy     │  - Credentials  │  - Config Watcher           │
│  - Schedule     │  - Profiles     │  - Trading Cycle            │
│  - Control      │  - Bot Config   │  - Risk Manager             │
│  - Dashboard    │  - Dashboard    │  - Alpaca Client            │
└─────────────────┴─────────────────┴─────────────────────────────┘
           │                │                     │
           └────────────────┴─────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  SQLite Database  │
                    │  (bot_data volume)│
                    └───────────────────┘
```

## Service Details

### Frontend
- **Technology**: React 18 + TypeScript + Vite + Tailwind CSS
- **Routing**: React Router v6
- **State**: React Hook Form + Zod validation
- **Charts**: Recharts for equity curve visualization
- **Theming**: CSS custom properties, declared once for light (`:root`) and once for dark
  (`.dark`), exposed to Tailwind as semantic colour names. `darkMode: 'class'`, toggled by a
  class on `<html>`. A blocking script in `index.html` applies the class before first paint to
  avoid a flash. The `dark:` variant is used nowhere - components name a role, not a value.
- **API Client**: Axios with JWT interceptor
- **Build**: Multi-stage Docker build (Node builder → Nginx runtime)

### Backend
- **Framework**: FastAPI with Uvicorn ASGI server
- **Database**: SQLAlchemy 2.0 + SQLite (async not needed for SQLite)
- **Authentication**: two principals against one endpoint set
  - *user*: single password → bcrypt → JWT (`app/security.py`, `app/routes/auth.py`)
  - *agent*: `oq_`-prefixed key → sha256 lookup → scope check (`app/authz.py`)
  `get_current_principal` resolves either. Routes declare `require_scope(...)` for the scopes
  they accept, or `require_human()` where an agent key must never be admitted.
- **Encryption**: Fernet (AES-128-GCM) for Alpaca API credentials
- **API**: RESTful endpoints with Pydantic validation

### Worker
- **Scheduler**: APScheduler with cron trigger, pinned to `BOT_TIMEZONE` (Eastern by default)
- **Pattern**: Background scheduler with dynamic job management
- **Trading Cycle**: Runs on schedule, checks market hours, executes strategies
- **Async Model**: `BackgroundScheduler` runs jobs in a thread and does not await coroutines, so
  `_run_trading_cycle` is a sync entry point wrapping one `asyncio.run()` around the whole cycle
- **Risk Management**: Position sizing, daily loss limits, concurrent position limits, kill-switch.
  The daily loss figure comes from the broker (`equity` vs `last_equity`), not from local
  `TradeLog.pnl`, which is never written. Limits gate new buys but never block sells.

## Data Flow

### Trading Cycle Execution
```
1. Scheduler triggers _run_trading_cycle()
2. Load active BotConfig and StrategyProfile
3. Check market clock (skip if closed)
4. Get account equity from Alpaca
5. Get current positions from Alpaca
5. Run strategy.generate_signals() for each symbol
6. For each signal:
   a. Validate with RiskManager
   b. Check if position already exists
   c. Submit order via Alpaca API
   d. Log trade to database
7. Record equity snapshot
```

### Configuration Flow
```
1. User configures via Frontend pages
2. Frontend calls Backend REST API
3. Backend stores in SQLite
4. Worker polls for config changes every 30s
5. Worker dynamically adds/removes cron jobs
```

## Security Model

### Credentials Storage
- Alpaca API keys encrypted at rest using Fernet
- Encryption key from `SECRET_ENCRYPTION_KEY` environment variable
- Keys never logged or exposed to frontend

### Authentication
- Single-password bcrypt hash in `APP_PASSWORD_HASH`
- JWT tokens for session management (24hr expiry)
- Tokens stored in localStorage, sent via Authorization header

### Agent authorization
The admin credential is unsuitable for a machine caller: its actions are untraceable, it
cannot be revoked without locking the human out, and it is all-or-nothing on the kill switch.
Agent access is therefore a separate credential in a separate table, accepted by the same
routes.

- Key format `oq_` + 32 bytes of `token_urlsafe`. Stored as a **SHA-256 hash** plus an
  11-char `key_prefix` used as the lookup handle; the plaintext is returned once at creation
  and is unrecoverable afterwards.
- Scopes: `read`, `config:write`, `bot:control`, `bot:kill`. Nothing is granted implicitly.
  A new key gets `read` only; `bot:kill` is never a default.
- Revocation is soft (`revoked_at`) so the row remains as evidence the key existed. A revoked
  key resolves to no principal, and the next request is `401`.
- `last_used_at` is written on every successful agent authentication.
- **Human-only by construction:** `/credentials` (overwrites the broker key) and `/agent-keys`
  (mints access) both use `require_human()`. An agent that could reach either could take the
  account or escalate itself, which would make the scope model decorative.
- There is no agent-facing order-placement endpoint. An agent can reconfigure a bot which
  places orders, but cannot itself place one.

### Rate limiting
Agent keys are long-lived bearer tokens, so they need a brake the admin credential does not.

- Fixed-window counters in the backend process, keyed on the **resolved key id**. Keying on the
  token or its prefix would let anyone who knows a prefix starve a real key by guessing under
  it.
- Windows are **aligned to the clock**, not opened on a client's first request, so a caller
  straddling a boundary cannot get two budgets.
- Defaults: 120 reads and 20 writes per minute (`AGENT_READ_RATE_LIMIT`,
  `AGENT_WRITE_RATE_LIMIT`). Over budget is `429` with `Retry-After`.
- **The admin session is not limited.** One human in one browser is not the threat model.
- State is per process: exact for one replica, incorrect behind a load balancer with two. That
  case would need the shared SQLite volume, not a second in-memory copy.

### Audit trail
Once an agent can move the bot, "was that the human or the agent" stops being obvious, and
`last_used_at` cannot answer it - it records that a key was *presented*, not what it did, and
stops recording the moment the key is revoked.

- `app/audit.py` middleware writes one row per state-changing request. Reads are not recorded:
  the dashboard polls, and a trail of polls is a trail nobody reads.
- The actor comes from `request.state`, which `get_current_principal` sets on success. A
  request that never authenticated is recorded as `anonymous` - which includes refused and
  expired-token attempts, the ones worth seeing.
- **Request bodies are never captured.** A route opts in to detail via
  `audit.record_summary(request, ...)`, which is what keeps the Alpaca secret key out of a
  table any `read` key can fetch back.
- A failed audit write is logged and swallowed. The action has already been applied; failing
  the request would turn a logging fault into a trading fault.
- Readable at `GET /dashboard/audit-log` (scope `read`), filterable by actor.

### Network
- Paper trading endpoint hardcoded (`https://paper-api.alpaca.markets`)
- No live trading possible by design
- CORS configured for frontend origin

## Database Schema

### Tables
- `api_credentials` - Encrypted Alpaca API keys
- `agent_keys` - Scoped agent credentials (label, key prefix, key hash, scopes, revoked_at)
- `audit_log` - State-changing API calls: actor kind/label, key id, method, path, action, status
- `strategy_profiles` - Strategy configurations with risk params
- `bot_config` - Global bot settings (schedule, active profile)
- `trade_logs` - All executed trades with PnL
- `equity_snapshots` - Periodic equity for curve charting

### Key Relationships
- `BotConfig.active_profile_id` → `StrategyProfile.id` (FK)
- `TradeLog.profile_id` → `StrategyProfile.id` (FK, nullable)
- `agent_keys` has no FKs; it is a standalone credential
- `audit_log.key_id` is a plain integer, not a FK. It deliberately survives the key it refers
  to being revoked, and a FK would make the trail's whole purpose impossible.

### Connection settings
The backend and the worker are separate containers sharing one SQLite file, so they contend by
default: the rollback journal takes a whole-database lock for the duration of any write, meaning
the worker's trade-log inserts and the backend's dashboard reads block each other. `build_engine()`
in `app/db.py` sets **WAL** (readers proceed during a write) and a **15s busy timeout** (contention
waits rather than raising). WAL is persistent in the file, so it is set once per engine;
`busy_timeout` is per-connection, so it goes in `connect_args`.

One consequence worth knowing: SQLite serialises writers, so any code path that writes on a read
request is expensive. `AgentKey.last_used_at` is stamped at most once a minute per key for exactly
this reason - see `AGENTS.md` gotcha 18.

### Column typing at the broker boundary
`trade_logs.status` and `alpaca_order_id` are free text (`String`), not enums, and the
`/dashboard/orders` response model types its fields as `str`. This is deliberate.

`alpaca.trading` returns 18 order statuses and SDK types, not JSON types: `order.id` is a
`uuid.UUID`, `qty` a `Decimal`, and side/type/status are enum members. Two failure modes came
from mapping that onto narrower types:

- An `Enum` column accepted an unknown status, because SQLAlchemy 2.0 emits no CHECK
  constraint by default. The row committed and then could not be loaded back, raising
  `LookupError` and 500ing `/dashboard/logs` on the first real trade.
- A `str` response field does not accept a `uuid.UUID` in Pydantic v2, so `/dashboard/orders`
  500'd on every order regardless of status.

So values are converted explicitly at the boundary: `local_order_status()` maps the broker's
status to the app's own `OrderStatus` vocabulary before the row is written, and every
`alpaca_client` return value is coerced in the route. `OrderStatus.submitted` is our own
label for "in flight" - Alpaca never sends it. For a live order's real status, read
`/dashboard/orders`; `alpaca_order_id` on the log row links the two.

## Deployment

### Requirements
- Docker and Docker Compose
- Alpaca Paper Trading API keys

### Environment Variables
All configured via `.env` file:
- `SECRET_ENCRYPTION_KEY` - 32-byte base64 Fernet key
- `APP_PASSWORD_HASH` - bcrypt hash of login password
- `DATABASE_URL` - SQLite path (default: `sqlite:///data/bot.db`)
- `JWT_SECRET` - JWT signing secret
- `BOT_SCHEDULE_CRON` - APScheduler cron expression (evaluated in `BOT_TIMEZONE`)
- `BOT_TIMEZONE` - IANA timezone for the cron expression (default: `America/New_York`)
- `AGENT_READ_RATE_LIMIT` - Agent key reads per minute (default: `120`)
- `AGENT_WRITE_RATE_LIMIT` - Agent key writes per minute (default: `20`)

### Startup
```bash
docker compose up --build
```

### Access
- Frontend: http://localhost
- Backend API: http://localhost:8000
- Health check: http://localhost:8000/health

## Monitoring & Observability

### Health Checks
- Backend: `/health` endpoint (used by Docker healthcheck)
- Frontend: Depends on backend healthy condition
- Worker: Logs startup status, no HTTP endpoint. The backend image bakes in a `HEALTHCHECK`
  for `localhost:8000/health`, which the worker can never satisfy, so `docker-compose.yml`
  disables it for the worker service. The scheduler logs job execution instead.

### Logging
- Structured logging via Python `logging` module
- Worker logs trading cycle events
- Backend logs API requests (via Uvicorn)

### Metrics
- Equity snapshots recorded each cycle
- Trade logs with PnL for performance analysis
- Market clock status for debugging

## Failure Scenarios & Mitigations

| Scenario | Mitigation |
|----------|------------|
| Market closed | Worker checks clock, skips cycle |
| API credentials invalid | Test connection endpoint, worker logs error |
| Daily loss exceeded | Kill-switch triggers, cancels all orders |
| Position limit reached | RiskManager blocks new buy orders |
| Database locked | WAL mode + 15s busy timeout, set in `app/db.py` (see below) |
| Worker crash | APScheduler coalesce=True, max_instances=1 |
| Config change | Worker polls every 30s, updates schedule dynamically |

## Scaling Considerations

- **Horizontal**: Not designed for horizontal scaling (single worker)
- **Vertical**: Increase container resources for more symbols
- **Database**: SQLite suitable for single-instance; migrate to PostgreSQL for HA
- **Frontend**: Static files served by Nginx, easily cached by CDN
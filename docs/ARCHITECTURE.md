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
│  - Control      │  - Bot Config   │  - Fill Reconciler          │
│  - Dashboard    │  - Dashboard    │  - Risk Manager             │
│  - Agents       │                 │  - Alpaca Client            │
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
- **Responsive layout**: below the `lg` breakpoint the sidebar is an off-canvas drawer owned by
  `Layout`, dismissed by link tap, backdrop, close button and Escape. Cards step down to `p-4`,
  wide tables get `overflow-x-auto` with a `min-w` so they scroll inside the page rather than
  pushing the whole document sideways, and the dashboard's tab strip scrolls with icon-only
  labels on small screens.
- **Dates and timezones**: the API emits explicit UTC (`UtcDatetime`, a `BeforeValidator` on every
  response timestamp) and the dashboard renders through `safeFormat(value, pattern, timeZone?)`
  in the zone the market-clock response reports. Two rules make this safe: a naive datetime is
  normalised once at the serialization boundary rather than in each call site, and the frontend
  never hardcodes "ET" - it renders what the API says, because the worker's zone is configurable.
- **Percentages**: every percentage input is in the units its label claims (1-100), and converts
  to the stored fraction at exactly one point on the way in and one on the way out. The API keeps
  fractions, because every risk limit is a fraction of equity.
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
- **Trading Cycle**: Runs on schedule, gates on market hours, executes strategies
- **Market hours**: `app/bot/market_hours.py`. The broker's `is_open` decides *whether* trading is
  possible (it knows holidays and half-day closes); the wall clock in `MARKET_TZ` only subdivides
  that into pre-market / regular / after-hours, which is what `market_hours_only` selects on and
  what `/dashboard/market-clock` reports back.
- **Async Model**: `BackgroundScheduler` runs jobs in a thread and does not await coroutines, so
  `_run_trading_cycle` is a sync entry point wrapping one `asyncio.run()` around the whole cycle
- **Risk Management**: Position sizing, daily loss limits, concurrent position limits, kill-switch.
  The daily loss figure comes from the broker (`equity` vs `last_equity`), not from local
  `TradeLog.pnl`, which is never written. Limits gate new buys but never block sells.
- **Capital base**: `RiskManager.investable_equity` is `equity x capital_allocation_pct`, and it
  is the base for sizing *and* every risk limit. Applying the allocation to sizing alone would
  leave the limits measured against the whole account, silently converting a 15% cap into 18.75%
  of the money the operator asked to be investable.
- **Deployment ceiling**: `min(1, max_concurrent x min(max_position, position_size))`. Exposed as
  `StrategyProfile.max_deployable_pct` so the amount of capital the caps make *unreachable* is
  visible rather than inferred from an idle balance.
- **Fill reconciler**: a 1-minute job re-reading orders that filled after their cycle closed.
  Registered unconditionally rather than under `is_running` - an order from the last cycle before
  a stop still fills, and that is exactly the order a "running" gate would miss. Bounded to the
  last 48h, since Alpaca's order history is a bounded window and everything older is a permanent
  404.
- **Schema**: the worker calls `init_db()` itself. See "Schema management" below - the backend
  doing it is not enough, because the worker is a separate process on a shared file.

## Data Flow

### Trading Cycle Execution
```
1. Scheduler triggers _run_trading_cycle()
2. Load active BotConfig and StrategyProfile
   (the worker filters on BOTH active_profile_id and enabled - neither alone
    selects what trades)
3. Gate on the market clock: skip unless the broker reports the session is open,
   and - when market_hours_only is set - unless it is the *regular* session
4. Get account equity and positions from Alpaca
5. Compute investable_equity = equity x capital_allocation_pct
6. Run strategy.generate_signals() for each symbol
7. For each signal:
   a. Validate with RiskManager
   b. Check if position already exists
   c. Submit order via Alpaca API
   d. Record the fill, if the order has one yet
   e. Log trade to database
8. If the profile has cash_sweep enabled and the strategy found no signal,
   spend a bounded slice of undeployed cash on the sweep symbol - through the
   same _process_signal, so it takes the same risk checks and the same log write
9. Record equity snapshot
```

Steps 3 and 8 are separate gates for a reason. The clock is about *when*; the sweep is about
*whether there is anything to buy*. Doing the sweep inside the signal loop would have meant a
second, quieter path to the broker - the exact shape of bug that gotcha 11 describes, where a
logging failure reads as a trading failure.

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
- `strategy_profiles` - Strategy configurations with risk params, plus `allow_fractional_shares`
- `bot_config` - Global bot settings (schedule, active profile, `market_hours_only`,
  `capital_allocation_pct`)
- `trade_logs` - All executed trades, with `filled_price`/`filled_qty` once the broker reports them
- `equity_snapshots` - Periodic equity for curve charting

`cash_sweep` is deliberately *not* a column: it lives inside `strategy_profiles.parameters` as an
opt-in JSON block, because it is a strategy choice rather than a risk limit, and an operator who
never wants it should not have a nullable column for it.

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

### Schema management
There is no Alembic environment - `alembic` is in `requirements.txt` and nothing imports it.
`app/db.py` provides the whole upgrade path:

1. `Base.metadata.create_all()` - creates missing *tables*
2. `ensure_schema()` - idempotent `ALTER TABLE ... ADD COLUMN` for columns the models have gained

Step 1 never touches the columns of a table that already exists, which is why step 2 exists:
adding `bot_config.capital_allocation_pct` as a model change alone produced a backend and a
worker that both died on their first query with `no such column`, on a database full of real
trades. Every added column carries a `server_default`, because SQLite's `ADD COLUMN` leaves
existing rows NULL - without one the feature works on new rows and aborts the cycle on the
account you already have.

**Both containers call `init_db()`.** The worker is a separate process with its own engine and
connection pool on the same file, and it cannot wait for the backend: `depends_on:
service_started` waits for the container, not for the startup hook, and `docker compose restart
worker` starts it with nothing else running. Two callers then race on `create_all`, which is a
read-then-create with no `IF NOT EXISTS` behind it, so `init_db()` retries - and only on
`already exists`, since a genuine `OperationalError` must still surface immediately.

This is deliberately narrow: ADD COLUMN only, no down-migration, no backfill beyond the column's
default. A rename or a type change needs real Alembic.

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
| Market closed | Worker checks the broker clock, skips cycle; `skip_reason()` says which check stopped it |
| Pre-market / after-hours with `market_hours_only` on | Broker `is_open` alone permits the session, so the wall clock in `MARKET_TZ` is used to require the *regular* one |
| API credentials invalid | Test connection endpoint, worker logs error |
| Daily loss exceeded | Kill-switch triggers, cancels all orders |
| Position limit reached | RiskManager blocks new buy orders |
| Database locked | WAL mode + 15s busy timeout, set in `app/db.py` (see below) |
| Worker crash | APScheduler coalesce=True, max_instances=1 |
| Config change | Worker polls every 30s, updates schedule dynamically |
| New column deployed | `init_db()` in *both* containers; the worker crashes on `no such column` otherwise, and only the backend upgrade is not enough |
| Order fills after its cycle closed | 1-minute fill reconciler re-reads unresolved orders (last 48h) |
| Order expired from Alpaca's history | Reconciler logs at debug and skips; a permanent 404 is normal operation, not an error |
| Capital idle because of caps | `max_deployable_pct` shown on the active profile and in the configurator; `capital_allocation_pct` and opt-in fractional/sweep available |

## Scaling Considerations

- **Horizontal**: Not designed for horizontal scaling (single worker)
- **Vertical**: Increase container resources for more symbols
- **Database**: SQLite suitable for single-instance; migrate to PostgreSQL for HA
- **Frontend**: Static files served by Nginx, easily cached by CDN
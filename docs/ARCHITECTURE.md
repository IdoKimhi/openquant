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
- **API Client**: Axios with JWT interceptor
- **Build**: Multi-stage Docker build (Node builder → Nginx runtime)

### Backend
- **Framework**: FastAPI with Uvicorn ASGI server
- **Database**: SQLAlchemy 2.0 + SQLite (async not needed for SQLite)
- **Authentication**: JWT tokens with bcrypt password hashing
- **Encryption**: Fernet (AES-128-GCM) for Alpaca API credentials
- **API**: RESTful endpoints with Pydantic validation

### Worker
- **Scheduler**: APScheduler with cron trigger
- **Pattern**: Background scheduler with dynamic job management
- **Trading Cycle**: Runs on schedule, checks market hours, executes strategies
- **Risk Management**: Position sizing, daily loss limits, concurrent position limits, kill-switch

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

### Network
- Paper trading endpoint hardcoded (`https://paper-api.alpaca.markets`)
- No live trading possible by design
- CORS configured for frontend origin

## Database Schema

### Tables
- `api_credentials` - Encrypted Alpaca API keys
- `strategy_profiles` - Strategy configurations with risk params
- `bot_config` - Global bot settings (schedule, active profile)
- `trade_logs` - All executed trades with PnL
- `equity_snapshots` - Periodic equity for curve charting

### Key Relationships
- `BotConfig.active_profile_id` → `StrategyProfile.id` (FK)
- `TradeLog.profile_id` → `StrategyProfile.id` (FK, nullable)

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
- `BOT_SCHEDULE_CRON` - APScheduler cron expression

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
| Database locked | SQLite WAL mode, connection pooling |
| Worker crash | APScheduler coalesce=True, max_instances=1 |
| Config change | Worker polls every 30s, updates schedule dynamically |

## Scaling Considerations

- **Horizontal**: Not designed for horizontal scaling (single worker)
- **Vertical**: Increase container resources for more symbols
- **Database**: SQLite suitable for single-instance; migrate to PostgreSQL for HA
- **Frontend**: Static files served by Nginx, easily cached by CDN
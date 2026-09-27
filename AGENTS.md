# Alpaca Paper Trading Bot - Project Context for AI Agents

## Project Overview
A Dockerized, self-hosted paper-trading application for Alpaca's paper trading API. Built with FastAPI (backend), React/TypeScript/Vite/Tailwind (frontend), and APScheduler (worker).

**Key Constraint:** Paper trading only - connects exclusively to `https://paper-api.alpaca.markets`. Live trading is impossible by design.

## Architecture
- **Frontend:** Nginx + React SPA (port 80)
- **Backend:** FastAPI + Uvicorn (port 8000)
- **Worker:** APScheduler in separate container, shares SQLite volume
- **Database:** SQLite on named volume (`bot_data:/data`)
- **Auth:** JWT tokens + bcrypt password hashing
- **Secrets:** Fernet encryption (AES-128-GCM) for Alpaca API keys

## Project Structure
```
/opt/alpaca-paper-bot/
├── docker-compose.yml
├── .env.example
├── .env (gitignored)
├── AGENTS.md
├── README.md
├── test_docker_compose.sh
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py              # FastAPI app, health endpoint, router registration
│   │   ├── config.py            # Pydantic Settings
│   │   ├── security.py          # Fernet, JWT, bcrypt
│   │   ├── db.py                # SQLAlchemy + SQLite (lazy engine)
│   │   ├── models.py            # ORM models
│   │   ├── alpaca_client.py     # Paper-only Alpaca wrapper
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── bot/
│   │   │   ├── engine.py        # Strategy execution
│   │   │   ├── risk.py          # RiskManager: sizing, daily loss, concurrency, kill-switch
│   │   │   └── strategies/
│   │   │       ├── base.py      # BaseStrategy, Signal dataclass
│   │   │       ├── __init__.py  # STRATEGY_REGISTRY
│   │   │       ├── sma_crossover.py
│   │   │       ├── rsi_reversion.py
│   │   │       └── momentum_breakout.py
│   │   └── routes/
│   │       ├── auth.py          # prefix /auth
│   │       ├── credentials.py   # prefix /credentials
│   │       ├── profiles.py      # prefix /profiles
│   │       ├── bot_config.py    # prefix /bot
│   │       └── dashboard.py     # prefix /dashboard
│   ├── tests/                   # 11 files, 63 tests, all passing
│   └── worker/
│       ├── main.py              # entrypoint (python -m worker.main)
│       └── scheduler.py         # BotWorker: APScheduler jobs, trading cycle
├── frontend/
│   ├── nginx.conf               # strips /api, proxies to backend
│   └── src/
│       ├── api/                 # axios modules, one per router
│       ├── components/          # Layout, Sidebar
│       ├── hooks/useAuth.tsx    # AuthContext
│       └── pages/               # Setup, Strategy, Schedule, Control, Dashboard
└── docs/
    └── ARCHITECTURE.md
```

## Current State
All 19 planned tasks are implemented and verified:
- ✅ Tasks 1-9: scaffold, core, alpaca client, auth, credentials, profiles, validation, bot config, dashboard
- ✅ Task 10: strategy tests (`test_strategies.py`, `test_strategy_params.py`)
- ✅ Task 11: `bot/risk.py` + `test_risk.py`
- ✅ Task 12: `worker/main.py` + `worker/scheduler.py`
- ✅ Tasks 13-17: all 5 frontend pages
- ✅ Task 18: full stack builds and runs; 63/63 tests pass
- ✅ Task 19: `docs/ARCHITECTURE.md`, `.gitignore`, README

There is no known failing functionality. Keep this file in sync with reality - it previously
described finished work as "NOT STARTED", which wasted effort and hid real bugs.

## Gotchas That Have Caused Bugs

**1. Never put `/api` in a `TestClient` URL.**
`/api` is an nginx concern. `nginx.conf` does `location /api/ { proxy_pass http://backend:8000/; }`,
which strips the prefix before the request reaches FastAPI. The app itself only serves
`/profiles`, `/dashboard/account`, etc. A test hitting `/api/profiles` gets a 404 - this
previously broke 20 tests. Test URLs must be the bare app path.

**2. The dashboard router is mounted at `/dashboard`.**
`dashboard.ts` must call `/dashboard/account`, not `/account`. Omitting the prefix 404s too -
it is a different mistake from #1, and the two bugs have opposite fixes.

**3. Keep frontend interfaces in `api/*.ts` in sync with the Pydantic response models.**
They are hand-written and drift silently. A missing field usually renders as `undefined`
(currency formatters produce `"$NaN"`, they do not throw). The dangerous case is a *date*:
`format()` and `formatDistanceToNow()` from `date-fns` throw `RangeError: Invalid time value`
on an invalid `Date`, which unmounts the entire page and shows a blank white screen with no
error message. This is how the dashboard broke when `MarketClockResponse` omitted `timestamp`.
`DashboardPage` now routes every date through `safeFormat`/`safeFormatDistance`, which return
`'-'` instead of throwing. Apply the same pattern to any new date rendering. Note the backend
names the order-type field `order_type`, not `type`.

**4. `HTTPBearer` returns 403, not 401, when the header is missing entirely.**
A 403 means "no Authorization header was sent". The axios interceptor in `api/client.ts` only
handles 401 (expired/invalid token); do not broaden it to 403 or a transient header bug will
nuke a valid session.

**5. Do not re-add a healthcheck to the worker.**
The backend image bakes in `HEALTHCHECK` curling `localhost:8000/health`. The worker serves no
HTTP, so it fails forever and reports a healthy scheduler as `unhealthy`. `docker-compose.yml`
disables it for the worker with `healthcheck: disable: true`.

## Development Workflow
- **TDD mandatory:** Write failing tests first, then implementation
- **Run tests:** `cd backend && pytest -v` (needs a host Python with the deps)
- **Build:** `docker compose up --build` from project root
- **Typecheck:** `cd frontend && npx tsc --noEmit`
- **Environment:** Copy `.env.example` to `.env`, generate secrets

### Running the tests inside the container
`conftest.py` uses the relative SQLite URL `sqlite:///./test.db`, but `/app` is root-owned while
the container runs as `appuser`, so running pytest from `/app` fails with
`sqlite3.OperationalError: unable to open database file`. Run from a writable CWD instead:
```bash
docker cp backend/tests <container>:/app/tests
docker exec -u root <container> chown -R appuser:appuser /app/tests
docker compose exec backend sh -c 'cd /tmp && python -m pytest /app/tests -q'
```
Note the image ships only `app/` and `worker/`, not `tests/`.

## Key Technical Decisions
1. **Single global cron schedule** (not per-profile) - `BOT_SCHEDULE_CRON` in `.env`
2. **Separate worker container** - APScheduler polling the shared SQLite volume
3. **Paper-only enforcement** - `AlpacaClient` ignores any base_url override
4. **Encrypted credentials** - Fernet with key from `SECRET_ENCRYPTION_KEY`
5. **No secrets in logs/source** - all via env vars
6. **Single-password auth** - one bcrypt hash in `APP_PASSWORD_HASH`, JWT session after login

## API Surface
| Prefix | Endpoints |
|--------|-----------|
| `/auth` | `POST /login`, `GET /verify` |
| `/credentials` | `POST /`, `GET /status`, `POST /test` |
| `/profiles` | `GET/POST /`, `GET/PATCH/DELETE /{id}`, `POST /{id}/activate` |
| `/bot` | `GET/PATCH /config`, `POST /start`, `/stop`, `/pause`, `/kill-switch` |
| `/dashboard` | `GET /account`, `/positions`, `/orders`, `/equity-curve`, `/logs`, `/market-clock` |
| `/health` | `GET /` (container healthcheck) |

## Commands
```bash
# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate password hash
python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"

# Run backend tests
cd /opt/alpaca-paper-bot/backend && pytest -v

# Typecheck frontend
cd /opt/alpaca-paper-bot/frontend && npx tsc --noEmit

# Build and run
cd /opt/alpaca-paper-bot && docker compose up --build
```

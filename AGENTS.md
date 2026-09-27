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
│   │   │       ├── base.py      # BaseStrategy, Signal, bars_request/lookback_start
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
│   ├── tests/                   # 13 files, 139 tests, all passing
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
- ✅ Task 18: full stack builds and runs; 139/139 tests pass
- ✅ Task 19: `docs/ARCHITECTURE.md`, `.gitignore`, README
- ✅ Trading-path audit: the bot had never executed. **Nine** independent bugs found and
  regression-tested by actually running the cycle:
  1-4. missing `await`s throughout, wrong `decrypt_value` import, wrong `key_id` kwarg, and
  bar requests with no `start` (so every strategy saw zero bars and could never signal)
  5. the daily loss limit and kill-switch were permanently inert - they read `TradeLog.pnl`,
  which nothing ever writes
  6. the scheduler ran in UTC, so a market-hours cron fired 05:00-12:00 ET
  7. `TradeLog.status` typed as a 5-value enum while Alpaca sends 18. The write committed
  (no CHECK in SQLite) and the row became unloadable, 500ing both `/dashboard/orders` and
  `/dashboard/logs`
  8. `OrderResponse.id` declared `str` while `order.id` is a `uuid.UUID` - `/dashboard/orders`
  500'd on *every* order
  9. `alpaca_order_id=order.id` passed a `uuid.UUID` to sqlite3, so the first real order was
  placed at the broker and then logged as "Order failed"

There is no known failing functionality. Keep this file in sync with reality - it previously
described finished work as "NOT STARTED", which wasted effort and hid real bugs.

**Every one of those nine was invisible to the test suite.** `worker/scheduler.py` had no tests
at all, and the strategy tests mocked the Alpaca client to always return bars. Bugs 7-9 were
only found after building a test that drives `_process_signal` and reads the row back through
the ORM. Do not add a feature to the trading path without a check that runs against real
market data, and do not trust a mock that returns convenient types.

## Gotchas That Have Caused Bugs (API and frontend)

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

**5b. The broker returns SDK types, not JSON types. Coerce every one at the route.**
`alpaca.trading` hands back `order.id` as a `uuid.UUID`, `qty` as a `Decimal`, and
`side`/`order_type`/`status` as enum members. Pydantic v2 does **not** coerce these to
`str`/`float` for you, and a `response_model` will reject them with
`ResponseValidationError` -> HTTP 500. `/dashboard/orders` had `str()` on every field
*except* `id`, so it 500'd on every order the moment one existed. The rule: anything
coming out of `alpaca_client` that lands in a response dict gets converted explicitly.
`TradeLog` writes are worse, because the row is already committed by the time anyone
notices - see gotcha 10.

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

When copying `app/`, `worker/` or `tests/` into a running container, `rm -rf` the destination
first. `docker cp src dst` treats an existing `dst` directory as a parent and creates
`dst/src_basename`, so a stale copy silently keeps running the old code.

### Verifying against the real API, not just mocks
Three separate bugs shipped here while every unit test passed, because the tests mocked the
Alpaca client to always return happy-path data. When a bug is "the bot does nothing", unit
tests cannot find it. Drive the real code against the real paper account with only the
destructive call (`submit_order`) patched, and assert on what actually came back. See
"Verifying the trading path" below.

## Gotchas That Have Caused Bugs (runtime)

**6. Alpaca's bars endpoint returns nothing unless you pass `start`.**
A `StockBarsRequest` with only `limit` comes back `HTTP 200` with an empty payload - no error,
no warning. Every strategy built its request that way, so all three hit their
"insufficient bars" branch on every symbol on every run and the bot could never emit a signal.
Always build requests through `bars_request()` in `strategies/base.py`, which derives a
lookback wide enough to cover `limit` bars. A `start` alone is not enough - too narrow a window
returns nothing either, which is why `lookback_start()` scales by timeframe.

**7. `TradeLog.pnl` is never written, so anything reading it sees zero.**
`RiskManager.check_daily_loss` summed `TradeLog.pnl`, but no code path in the app ever assigns
it, so the daily loss limit and the kill-switch gate were permanently inert. The live figure
now comes from the broker: `compute_daily_loss_pct(equity, last_equity)` on the Alpaca account
(`last_equity` is the previous close, not `last_day_equity`). If you add P&L tracking, keep the
broker number as the source of truth for the limit.

**8. APScheduler defaults to `Etc/UTC`, so a market-hours cron fires at the wrong time.**
`*/5 9-16 * * MON-FRI` under UTC is 05:00-12:00 ET, which silently skipped the entire afternoon.
The scheduler is pinned via `build_scheduler()` / `MARKET_TZ` from the `bot_timezone` setting
(default `America/New_York`). Cron expressions in this app are always Eastern, and market-hours
labels in the UI assume that.

**9. `BackgroundScheduler` does not await coroutine jobs.**
`AlpacaClient` methods and `run_strategy` are `async`. APScheduler runs jobs in a plain worker
thread, so a coroutine function registered as a job is never awaited - it just creates and
discards a coroutine. `_run_trading_cycle` stays a sync entry point that calls
`asyncio.run()` once around the whole cycle; do not make the job itself `async`. One loop per
cycle matters, since repeatedly calling `asyncio.run()` per request would churn event loops
underneath the SDK's HTTP client.

**10. Alpaca's order-status vocabulary is 18 values; ours is 5. Translate at the boundary.**
Alpaca sends `new`, `accepted`, `partially_filled`, `pending_new`, `expired`, `done_for_day`...
None of those except `filled`/`canceled`/`rejected` exist in `app.models.OrderStatus`, and
`submitted` is our own label that Alpaca never sends. Writing `order.status` straight into
`TradeLog.status` used to **commit successfully** - SQLAlchemy 2.0 emits no CHECK constraint by
default, so SQLite happily stored `accepted` in a column the ORM could then not load back
(`LookupError`). Two separate failures came out of that:
- `GET /dashboard/orders` 500'd, because `OrderResponse.status` was typed as the local enum and
  Pydantic rejected `accepted`.
- `GET /dashboard/logs` 500'd on the first real trade, because materialising the `TradeLog` row
  coerces the value into the enum.

Fixes: `local_order_status()` in `worker/scheduler.py` maps broker -> local, `TradeLog.status`
is a `String` column, and both response models use `str`. The column was never a real
constraint, so dropping the enum costs nothing and needs no migration on SQLite.
`test_order_status_vocabulary.py` pins all of it, including a check that the worker writes a
row the ORM can read back - a test on the mapping function alone passes happily while the call
site regresses, which is how the original bug survived.

**11. Converting an order to a log row happens *after* the order is live.**
`submit_order` is awaited first; only then is the `TradeLog` written. Any failure in that write
is caught by the same `except` that wraps order placement, so a logging bug reads as a *trading*
bug: the order really went to the broker, the app logs "Order failed", and the in-cycle position
bookkeeping is skipped. This is exactly what `alpaca_order_id=order.id` did, because a
`uuid.UUID` cannot be bound by sqlite3 (`ProgrammingError`) - see gotcha 5b. When a "failed"
trade appears in the log, check the broker before believing it.

## Verifying the trading path
The trading cycle had never executed. Nine independent bugs have now been found by actually
running it (four in the cycle itself, then the empty-bars bug, the inert kill-switch, the UTC
schedule, and the order-status/UUID boundary bugs). All are regression-tested, but the lesson
stands: unit tests that mock the Alpaca client cannot see any of this.

To re-verify, drive the real code and let only `submit_order` be stubbed. **The stub must
return the same types the real one does** - a `uuid.UUID` id and a real
`alpaca.trading.enums.OrderStatus` member. A stub returning a plain string id and a bare
status string is what hid bugs 10 and 11 for a whole release.

Split it in two, because only one part can be forced:

```python
# A: data path - nothing mocked at all. May legitimately produce 0 signals.
sigs = asyncio.run(run_strategy(profile, alpaca))

# B: order path - strategy stubbed to return one real Signal, rest real.
with patch.object(AlpacaClient, 'submit_order', new=fake_submit), \
     patch.object(AlpacaClient, 'get_clock', new=_clock_sync), \
     patch('worker.scheduler.run_strategy', new=injected_strategy):
    worker._run_trading_cycle()
```

`get_clock` must be patched to report `is_open=True`, otherwise the cycle returns early on a
weekend and you will conclude there is nothing to see. `fake_submit_order` and `_clock_sync` are
`async`-shaped: the worker `await`s them, so return a completed future, not a coroutine.

Two cautions: the cycle writes to the real `trade_logs` and `equity_snapshots` tables, so delete
those rows afterwards. And patching `run_strategy` hides bug #6 - use real
`alpaca.data.get_stock_bars` via `bars_request()` when you suspect the strategies return no
signals. Finally, **read the row back through the ORM** (`row.status`, not raw SQL). A commit
that succeeds can still have written a value the ORM cannot load, and only a real read catches
that.

## Key Technical Decisions
1. **Single global cron schedule** (not per-profile) - `BOT_SCHEDULE_CRON` in `.env`, evaluated
   in `BOT_TIMEZONE` (Eastern by default)
2. **Separate worker container** - APScheduler polling the shared SQLite volume
3. **Paper-only enforcement** - `AlpacaClient` ignores any base_url override
4. **Encrypted credentials** - Fernet with key from `SECRET_ENCRYPTION_KEY`
5. **No secrets in logs/source** - all via env vars
6. **Single-password auth** - one bcrypt hash in `APP_PASSWORD_HASH`, JWT session after login
7. **Risk limits are gates, not flatteners** - `validate_order` blocks new buys once the daily
   loss limit is breached but deliberately always permits sells, so a breach can never trap a
   position. Only `POST /bot/kill-switch` closes positions.

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

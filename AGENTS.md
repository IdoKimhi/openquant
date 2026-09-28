# OpenQuant - Project Context for AI Agents

## Project Overview
A Dockerized, self-hosted paper-trading application for Alpaca's paper trading API, with a
scoped key system so external AI agents can drive it. Built with FastAPI (backend),
React/TypeScript/Vite/Tailwind (frontend), and APScheduler (worker).

**Key Constraint:** Paper trading only - connects exclusively to `https://paper-api.alpaca.markets`. Live trading is impossible by design.

## Architecture
- **Frontend:** Nginx + React SPA (port 80)
- **Backend:** FastAPI + Uvicorn (port 8000)
- **Worker:** APScheduler in separate container, shares SQLite volume
- **Database:** SQLite on named volume (`bot_data:/data`)
- **Auth:** two principals, one endpoint set
  - human: single password -> bcrypt verify -> JWT (`app/routes/auth.py`)
  - agent: `oq_`-prefixed key -> sha256 lookup -> scope check (`app/authz.py`)
- **Secrets:** Fernet encryption (AES-128-GCM) for Alpaca API keys
- **Theming:** CSS custom properties on `:root` / `.dark`, toggled by a class on `<html>`

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
│   │   ├── security.py          # Fernet, JWT, bcrypt  (authentication)
│   │   ├── authz.py             # Principal, scopes, agent key gen  (authorization)
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
│   │       ├── credentials.py   # prefix /credentials   (human only)
│   │       ├── profiles.py      # prefix /profiles
│   │       ├── bot_config.py    # prefix /bot
│   │       ├── dashboard.py     # prefix /dashboard
│   │       └── agent_keys.py    # prefix /agent-keys    (human only)
│   ├── tests/                   # 15 files, 175 tests, all passing
│   └── worker/
│       ├── main.py              # entrypoint (python -m worker.main)
│       └── scheduler.py         # BotWorker: APScheduler jobs, trading cycle
├── frontend/
│   ├── nginx.conf               # strips /api, proxies to backend
│   ├── index.html               # contains the blocking pre-paint theme script
│   └── src/
│       ├── api/                 # axios modules, one per router
│       ├── components/          # Layout, Sidebar, ThemeToggle
│       ├── hooks/useAuth.tsx    # AuthContext
│       ├── hooks/useTheme.tsx   # ThemeProvider (light/dark)
│       ├── lib/                 # format.ts (non-throwing dates), chartColors.ts
│       └── pages/               # Setup, Strategy, Schedule, Control, Dashboard, Agents
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
- ✅ Task 18: full stack builds and runs; 143/143 tests pass
- ✅ Task 19: `docs/ARCHITECTURE.md`, `.gitignore`, README
- ✅ Trading-path audit: the bot had never executed. **Eleven** independent bugs found and
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
  10. `POST /profiles/{id}/activate` only flipped `StrategyProfile.enabled`, never
  `bot_config.active_profile_id` - which is the field the worker actually selects on. Adding a
  second profile and clicking "Activate" relabelled it Active in the UI while the bot kept
  trading the old one
  11. `PATCH /bot/config` with `active_profile_id` (which `SchedulePage` sends) left `enabled`
  on the previous profile, so the worker's two-part lookup matched nothing and *every* cycle
  bailed with "Active profile not found or disabled" - a silent trading stop
- ✅ Light/dark theme. All colour now resolves to a CSS custom property declared twice (`:root`
  and `.dark`); `darkMode: 'class'`; a blocking script in `index.html` sets the class before
  first paint. This also fixed a live bug: `tailwind.config.js` redefined
  `rounded-md`/`rounded-lg` as `var(--radius)`, and `--radius` was never defined anywhere, so
  **all 43 rounded corners in the app were rendering square** - every card, button, input and
  badge. The override is gone.
- ✅ Agent access. `oq_`-prefixed scoped keys in a new `agent_keys` table, accepted by the same
  routes as the admin JWT and gated per-route by `require_scope`. 32 tests in
  `test_agent_keys.py`, all four scope boundaries verified against the running stack.

There is no known failing functionality. Keep this file in sync with reality - it previously
described finished work as "NOT STARTED", which wasted effort and hid real bugs.

**Every one of those eleven was invisible to the test suite.** `worker/scheduler.py` had no
tests at all, and the strategy tests mocked the Alpaca client to always return bars. Bugs
7-9 were only found after building a test that drives `_process_signal` and reads the row
back through the ORM. Bugs 10-11 are worse: the existing `test_activate_profile` asserted on
`enabled` only, which is precisely the field that was wrong - a test written against the
implementation rather than the behaviour the worker depends on. Do not add a feature to the
trading path without a check that runs against real market data, and do not trust a mock that
returns convenient types.

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

**13. An agent key is not an admin token. `get_current_principal` is the one gate.**
`app/authz.py` resolves a bearer token to a `Principal` - either the admin (kind `user`, holds
every scope implicitly) or an agent key (kind `agent`, holds only its granted scopes). Every
route declares what it needs:

```python
@router.post("/kill-switch")
async def kill_switch(user=Depends(require_scope(SCOPE_BOT_KILL))): ...

@router.post("/credentials")
def store_credentials(data: CredentialsIn, user=Depends(require_human()): ...
```

Three things to keep true when you add a route:

- **A new route defaults to nothing.** Declaring no dependency makes it public. Every route
  takes either `require_scope(...)` or `require_human()`; there is no implicit protection.
- **A missing `Authorization` header is 403, not 401** (see gotcha 4). The axios interceptor in
  `client.ts` only reacts to 401, so do not "fix" this by treating 403 as an expired session.
  An agent key presented to `require_human` is *also* 403 - the token was valid, the principal
  simply is not a human. The two are indistinguishable by status code; the `detail` string
  differs.
- **Credentials and key management are human-only.** `POST /credentials` overwrites the broker
  key, and `POST /agent-keys` mints access. Both use `require_human()`. An agent that can reach
  either one can escalate itself or take the paper account outright.

Scopes are `read`, `config:write`, `bot:control`, `bot:kill`. `bot:kill` is never in
`DEFAULT_SCOPES`, and the `read` default is not a formality - `config:write` alone re-points
the bot at a different strategy, which is how a "read-only" bot starts trading something else.
`SCOPE_INFO` in `app/authz.py` is the single source of truth; `AgentsPage` builds its
checkboxes from `GET /agent-keys/scopes` rather than hardcoding the list.

Keys are `oq_` + 32 bytes of `token_urlsafe`, stored as a **sha256 hash** plus an 11-char
`key_prefix` used as the lookup handle. The plaintext exists only in the `POST /agent-keys`
response and is unrecoverable afterwards. Revocation is soft (`revoked_at`) so the row stays
visible as evidence the key existed; a revoked key authenticates to `None` and never updates
`last_used_at`.

**14. Never reintroduce a literal palette colour into a component.**
Every colour resolves to a CSS custom property declared twice in `index.css` (once under
`:root`, once under `.dark`), exposed through `tailwind.config.js` as semantic names -
`bg-surface`, `text-body`, `text-muted`, `text-subtle`, `border-line`, `bg-accent-soft`,
`border-line-danger`, `bg-danger-soft`, `text-danger-fg`. A `text-gray-900` is a light-theme-only
decision baked into a component and dark mode cannot reach it.

Three traps, all of which were live during this work:

- **`-fg` and `-contrast` are different colours.** `bg-danger-soft` takes `text-danger-fg` (the
  dark hue, for a pale fill). A solid `bg-danger` button takes `text-danger-contrast` (near
  white in light mode, *dark* in dark mode, because the fill is bright there). Using one for
  the other is an invisible-in-light-mode contrast failure.
- **`border-line-<colour>` vs `border-<colour>`.** The tinted alert panels want the soft edge;
  a selected row, tab underline or spinner wants the solid one. They are not interchangeable.
- **Recharts cannot use Tailwind classes.** It writes literal colours into SVG attributes, so
  `useChartColors()` in `lib/chartColors.ts` reads the `--chart-*` variables off `<html>` and
  re-reads them when the theme changes. A hardcoded hex here is a chart that stays light.

The `dark:` variant is used **nowhere** and should stay that way - if you find yourself adding
`dark:bg-slate-800`, the component is missing a semantic token instead. Note `darkMode: 'class'`
is required; Tailwind's default `'media'` cannot be overridden at runtime, so the toggle would be
unable to beat the OS.

**15. The pre-paint theme script in `index.html` must stay a blocking inline script.**
A user on a dark OS otherwise gets a full white flash, because CSS paints the light tokens
before React mounts and only then does the provider add `.dark`. Deferring that script, or
moving it into the bundle, restores the flash. It reads
`localStorage['openquant.theme']` and falls back to `prefers-color-scheme`;
`useTheme()` seeds its state from the class that script left on `<html>` rather than
reimplementing the same decision, so the two cannot disagree. **The storage key is duplicated
in both files - change one and you get a theme that resets every reload.**

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

**12. `StrategyProfile.enabled` and `bot_config.active_profile_id` are two different fields,
and the worker needs both.** The cycle resolves its profile with a two-part filter:

```python
StrategyProfile.id == bot_config.active_profile_id,
StrategyProfile.enabled == True
```

Neither field alone selects what trades. That produced two bugs at once (10 and 11 above), both
invisible from the UI because the frontend read one field and displayed it as "Active":
- `POST /profiles/{id}/activate` wrote only `enabled`, so the UI relabelled a profile Active
  while the bot kept trading whatever `active_profile_id` still pointed at.
- `PATCH /bot/config` with `active_profile_id` (sent by `SchedulePage`) wrote only that, so
  `enabled` stayed on the old profile and the two-part filter matched *nothing*. Every cycle
  logged "Active profile not found or disabled, skipping cycle" and the bot went quiet with no
  error in the UI at all - the worst failure mode here, because the dashboard just looks empty.

Both write paths now set both fields together, and the frontend reads
`active_profile_id` from `/bot/config` as the single source of truth for "currently trading".
**When you touch profile selection, resolve the profile the way the worker does** - filter on
both - or you will write a test that passes against the wrong field. `test_activate_profile`
did exactly that for a whole release: it asserted on `enabled`, which was the broken half.

## Verifying the agent path
The scope model is the kind of thing that passes its own tests while being wrong in the
direction that matters. A test that only checks "the read-only key can read" will happily pass
on a build where the read-only key can also liquidate, because nothing asserted the negative.
So the boundaries are asserted explicitly, in both directions, in `test_agent_keys.py`:
`config:write` and `bot:control` and `bot:kill` are each checked to be *refused* for a key that
lacks them, including `bot:control` not implying `bot:kill`, and `/credentials` plus
`/agent-keys` are checked to be refused for any agent key.

Two ways this goes wrong in practice:

- **Probing the live instance mutates it.** `POST /bot/start` and `/stop` write `is_running`,
  which persists. Verifying that an agent key *can* control the bot means starting your bot.
  Read the current value first, or probe against a throwaway profile, and put it back.
- **A 403 is ambiguous.** It means either "no Authorization header" or "valid agent key on a
  human-only route". Distinguish them by the `detail` string, not the status code, or a test
  will pass for the wrong reason.

## Verifying the trading path
The trading cycle had never executed. Eleven independent bugs have now been found by actually
running it (four in the cycle itself, then the empty-bars bug, the inert kill-switch, the UTC
schedule, and the order-status/UUID boundary bugs). All are regression-tested, but the lesson
stands: unit tests that mock the Alpaca client cannot see any of this.

To re-verify, drive the real code and let only `submit_order` be stubbed. **The stub must
return the same types the real one does** - a `uuid.UUID` id and a real
`alpaca.trading.enums.OrderStatus` member. A stub returning a plain string id and a bare
status string is what hid bugs 7-9 for a whole release. Two things this catches that nothing
else does: the stub has to be given a `self` (it is patched onto the class, so the worker
passes the instance positionally - forgetting it fails with "got multiple values for argument
'symbol'", which looks like an app bug and is not), and the row has to be read back through the
ORM rather than raw SQL.

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
6. **Two principals, one endpoint set** - the admin logs in with one password and gets a JWT;
   agents present an `oq_` scoped key. Both hit the *same* routes; there is deliberately no
   parallel `/agent/v1` surface, because a duplicated router drifts from the original
7. **Risk limits are gates, not flatteners** - `validate_order` blocks new buys once the daily
   loss limit is breached but deliberately always permits sells, so a breach can never trap a
   position. Only `POST /bot/kill-switch` closes positions.
8. **Least privilege by default** - a new agent key gets `read` and nothing else. `bot:kill` is
   never granted implicitly under any code path, and an agent key can never reach `/credentials`
   or `/agent-keys`.
9. **Theming via tokens, not variants** - colours are CSS custom properties, `.dark` redeclares
   them, and no component uses a `dark:` utility

## API Surface
Scopes: `read`, `config:write`, `bot:control`, `bot:kill`. "human" = admin session only,
never an agent key. The base URL an agent uses is `<origin>/api` - nginx strips `/api`.

| Prefix | Endpoints | Agent scope |
|--------|-----------|-------------|
| `/auth` | `POST /login`, `GET /verify` | public (password / bearer) |
| `/credentials` | `POST /`, `GET /status`, `POST /test` | **human** |
| `/agent-keys` | `GET/POST /`, `GET /scopes`, `DELETE /{id}` | **human** |
| `/profiles` | `GET /`, `GET /{id}` | `read` |
| `/profiles` | `POST /`, `PATCH/DELETE /{id}`, `POST /{id}/activate` | `config:write` |
| `/bot` | `GET /config` | `read` |
| `/bot` | `PATCH /config` | `config:write` |
| `/bot` | `POST /start`, `/stop`, `/pause` | `bot:control` |
| `/bot` | `POST /kill-switch` | `bot:kill` |
| `/dashboard` | `GET /account`, `/positions`, `/orders`, `/equity-curve`, `/logs`, `/market-clock` | `read` |
| `/health` | `GET /` (container healthcheck) | public |

There is no agent-facing "place an order" endpoint. The existing action surface is
start/stop/pause, kill-switch, profile and schedule changes, and reads. Adding direct order
placement is a separate piece of work and needs its own risk discussion - an agent that can
place orders is a different trust boundary from one that can reconfigure a bot which places
orders for itself.

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

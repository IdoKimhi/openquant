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
│   │   ├── authz.py             # Principal, scopes, agent key gen, RateLimiter  (authorization)
│   │   ├── audit.py             # AuditMiddleware + record_summary  (who changed what)
│   │   ├── db.py                # SQLAlchemy + SQLite (lazy engine) + ensure_schema migration
│   │   ├── models.py            # ORM models
│   │   ├── alpaca_client.py     # Paper-only Alpaca wrapper
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── bot/
│   │   │   ├── engine.py        # Strategy execution
│   │   │   ├── market_hours.py  # Session classification, trading_allowed, skip_reason
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
│   ├── tests/                   # 25 files, 398 tests, all passing
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
- ✅ Agent audit trail (`app/audit.py`, `audit_log` table). One row per state-changing call,
  whoever made it, with refused attempts recorded too. Reads are not recorded and request
  bodies are never captured. 14 tests in `test_agent_audit.py`.
- ✅ Agent rate limiting (`RateLimiter` in `app/authz.py`). Per-key, clock-aligned fixed
  windows, 120 reads / 20 writes per minute by default. The admin session is deliberately not
  limited. 15 tests in `test_agent_rate_limit.py`.
- ✅ A read no longer writes. `last_used_at` was stamped on every agent request, so every *read*
  took the SQLite write lock - and because authentication precedes the rate-limit check, even a
  429 wrote. At the 120 reads/min the limiter allows, 8-way concurrency returned
  `500 database is locked` on `GET /profiles` while the admin token (which never stamped it)
  returned 40 clean 200s. The stamp is now throttled to once a minute, and `app/db.py` sets WAL
  plus a 15s busy timeout. 6 tests in `test_agent_last_used_write.py`. See gotcha 18.
- ✅ Schedule page timezone. `GET /bot/config` now reports `cron_timezone` - the value the
  worker actually hands to `CronTrigger` - and the page renders it, instead of printing "UTC"
  and getting a `9-16` schedule four hours off. See gotcha 16.
- ✅ `market_hours_only` is enforced. It was stored, displayed, and read by nothing, while
  `is_open` alone let the bot trade 04:00-20:00 ET - so a position could open at 19:30 and sit
  overnight with no liquidity behind it. `app/bot/market_hours.py` now gates the cycle on the
  broker's clock for the regular session. See gotcha 17.
- ✅ Fill price and quantity are recorded, at submit time and by a 1-minute reconciler for the
  orders that fill after their cycle closed. All 52 historical rows are now `filled` with real
  prices. See gotcha 20.
- ✅ Capital deployment is visible and adjustable: `max_deployable_pct` exposes the ceiling,
  `capital_allocation_pct` is the "money to invest" base, fractional shares and a cash sweep
  are opt-in, and the defaults deploy more than they used to. See gotcha 21.
- ✅ Mobile layout. The drawer, the tables, the cards and the tab strip all work at 360px.
- ✅ A sell is sized by the position, not by the equity budget. All three strategies sized an
  exit exactly as they sized a buy, so every one of them asked to sell more shares than the
  account held; the broker refused each one (`40310000 insufficient qty available`), 22 times
  in a row on the live account, and three of seven positions had become impossible to close.
  `_process_signal` now clamps a sell to the holding, before risk validation. See gotcha 23.

There is no known failing functionality. Keep this file in sync with reality - it previously
described finished work as "NOT STARTED", which wasted effort and hid real bugs.

## Issues Closed 2026-09-29

Six reported issues. All fixed; all were reachable through the UI, and **three of them were
found by the fix for another one** rather than by looking for them.

**#3 - `market_hours_only` was never read.** New `app/bot/market_hours.py`.
`market_session_state()` classifies the session, `trading_allowed()` decides, `skip_reason()`
explains. The broker's `is_open` is authoritative - it knows about holidays and half-day
closes, which a wall clock does not - and the wall clock in `MARKET_TZ` subdivides it into
pre-market / regular / after-hours. 31 tests in `test_market_hours.py`.

The cause turned out to be a timezone bug wearing a trading bug's clothes. The trades logged
at "19:30-19:50 ET" were stored as naive UTC, which is **15:30-15:50 EDT** - the last half
hour of the regular session. The bot had never traded after hours; the dashboard rendered
naive UTC in the browser's zone. Fixing the timestamps (below) is what made #3 legible, and
either fix alone would have left the dashboard lying.

**#4 - equity-curve and timestamp timezone.** `UtcDatetime` in `app/schemas.py` is an
`Annotated[datetime, BeforeValidator]` on every response timestamp, so a naive datetime becomes
explicitly UTC *at the serialization boundary* rather than in six call sites that each have to
remember. `safeFormat(value, pattern, timeZone?)` in `lib/format.ts` renders it in a named zone
using the Intl wall-clock-shift technique, and the dashboard passes `marketClock.timezone` -
the value the worker actually uses - to every date it draws.

**#5 - fill price and quantity were never recorded.** `TradeLog.filled_price`/`filled_qty` were
NULL on all 52 rows in the live database. `_fill_from_order(order)` reads the fill off a broker
order and coerces the two `Decimal`s (gotcha 5b again). Submit-time capture gets the immediate
case; `_reconcile_fills` gets the rest, on a 1-minute job, because an order that lands at the
broker after the cycle closes is invisible to the cycle that placed it. 15 tests.

The reconciler found the opposite of what the data suggested. All 52 rows were `submitted` with
no fill, which reads as "the orders never executed" - but Alpaca's order history was still
there and all 52 came back **filled**. They had all executed; the app simply never looked. So
the reconciler is a first-class feature, not a backstop for a rare case.

**#2 - ~70% of capital deployed, by construction.** `min(1, max_concurrent x min(max_position,
position_size))`. Five positions at 15% is 75%, and no amount of waiting changes that. Four
remedies, all shipped:
- `StrategyProfile.max_deployable_pct` exposes the ceiling, so the number that explains the idle
  cash is visible instead of inferred. Shown on the active-profile card and in the configurator.
- Defaults raised to 0.15 / 10 positions, matching `app/schemas.py`. The frontend zod schema was
  at 0.10 / 5 while the API defaulted to 0.15 / 10, so **creating a profile by typing nothing
  built a profile capped at 50%** - the idle cash, arrived at by omission. The two copies of a
  schema must match.
- `allow_fractional_shares` (opt-in, default off) deploys the remainder instead of discarding up
  to a share per signal. Alpaca accepts fractional quantity on market orders only, so a
  fractional limit order is refused in `AlpacaClient.submit_order` by name.
- `cash_sweep` (opt-in, inside `parameters`) buys a broad instrument with a bounded slice of
  undeployed cash. It runs on every cycle **after** the signal loop, signal or no signal - the
  quiet week is the whole point, so gating it on "strategy found nothing" would make it fire
  precisely when the strategy was busy, which an early draft of this text claimed and the code
  never did. A cycle that did place signals lowers the sweep's budget automatically: the cycle
  sums the notional `_process_signal` reports and passes it as part of `deployed_value`, so the
  sweep measures spare against the book as it now is, not as it was at the top of the cycle.
  Routed through `_process_signal`, so it inherits the same risk checks and the same log write
  rather than being a second, quieter path to the broker. The log line is written *after*
  `_process_signal` returns, from what actually placed - it used to announce the sweep before
  the checks ran, so a sweep the concurrency cap then refused was logged as bought.

`RiskManager.investable_equity` applies `capital_allocation_pct` to **one** base that sizing and
every risk limit are measured against. Applying it to sizing alone would shrink the positions
while leaving the limits on the whole account, so a 15% cap would silently become 18.75% of the
money the operator asked to be investable. That invariant was false at the call site for a
while - the worker handed `validate_order` raw account equity, so the cap was measured against
the whole account while the strategies sized against 80% of it. `_process_signal` now takes
`investable_equity` *required*, and pins it; see `tests/test_risk_equity_base.py`.

**#6 - "money to invest".** `capital_allocation_pct` on `BotConfig`, defaulted to 1.0. The
frontend edits it as a percentage and sends a fraction, like every other percentage in the app.

**#1 - no mobile layout.** Below `lg` the sidebar was `-translate-x-full` with nothing to bring
it back, so the six nav links were unreachable on a phone. `Sidebar` now takes `open`/`onClose`,
lives in `Layout` beside the header that owns the flag, and dismisses on link tap, backdrop, the
close button and Escape. Cards go `p-4 sm:p-6`, tables get `overflow-x-auto` with a `min-w`, and
the dashboard's tab strip scrolls. `App.tsx` no longer mounts a second `<Sidebar/>`.

### Two bugs that only the *next* change exposed

**A schema upgrade that ran in one of two processes.** This project has no Alembic environment,
so `init_db` is the whole upgrade path - and it was only called from the backend's startup hook.
The worker is a separate container with its own engine and its own connection pool, so
deploying `bot_config.capital_allocation_pct` produced exactly the split the migration tests
were written to rule out: the backend's `/bot/config` served the new field correctly while the
worker crash-looped on `no such column`, raising inside `BotWorker.start()` before it ever
scheduled a cycle. `ensure_schema` is not enough on its own; **both processes must run it**,
and the worker cannot wait its turn - `depends_on: service_started` waits for the container, not
for the hook, and `docker compose restart worker` starts it with nothing else running. Two
callers then race on `create_all`, which is a read-then-create with no `IF NOT EXISTS` behind it,
so `init_db` retries - and only for `already exists`, because a genuine `OperationalError` must
still surface immediately. See gotcha 19.

**Five percentage fields that were fractions under a `(%)` label.** `Max Position Size (%)`,
`Max Daily Loss (%)`, `Position Size %` and `Max % of equity per cycle` were all bound to
`min="0.01" max="1"` - so typing `15` was refused by the input's own `max`, the box displayed
`0.15` where it said 15%, and a user trying to raise the ceiling for #2 would be fighting the
form. The new "Money to invest (%)" took 1-100, so two adjacent fields with identical labels
had opposite scales, which is a worse failure than either being consistently wrong.

The rule this earns: **a percentage input is in the units its label claims, all of them, always,
and the conversion to the stored fraction happens at exactly one place on the way in and one on
the way out.** `frac()`/`pct()` in `StrategyPage.tsx` are those two places. The tempting
alternative - keep the form in fractions and convert per input with react-hook-form's
`setValueAs` - is wrong, because `form.reset()` bypasses `setValueAs`: the same field would show
a percent after `handleEdit` and a fraction after `handleStrategyChange`, with no way to tell
which is which.

**Every one of those eleven was invisible to the test suite.** `worker/scheduler.py` had no
tests at all, and the strategy tests mocked the Alpaca client to always return bars. Bugs
7-9 were only found after building a test that drives `_process_signal` and reads the row
back through the ORM. Bugs 10-11 are worse: the existing `test_activate_profile` asserted on
`enabled` only, which is precisely the field that was wrong - a test written against the
implementation rather than the behaviour the worker depends on. Do not add a feature to the
trading path without a check that runs against real market data, and do not trust a mock that
returns convenient types.

## Issues Closed 2026-09-30

**#7 - credential rotation.** `POST /credentials` overwrote the stored key
unconditionally, and the secret is not recoverable from this app, so a typo
while rotating destroyed a working configuration with no way back. It now
validates against the broker *before* the write, and the failure modes are kept
apart, because the operator's correct response to each is different: 401/403 is
"go re-copy the key", 429 is "wait", and anything else is "we could not reach
Alaca" - which is not evidence about the key at all. `_probe` catches
`APIError` and *not* `Exception`, so a `TypeError` in our own code cannot be
reported to the operator as "invalid credentials". Also added:
`DELETE /credentials`, `POST /credentials/test-provided` (validate without
storing - the non-destructive half that makes the destructive half safe), and
`GET /credentials` returning the last four of the key id plus the store date,
which is the only way to tell a rotated key from the one it replaced. 25 tests
in `test_credentials.py`.

The UI was the bigger half of the gap: the credentials form rendered only
behind `{!hasCredentials && ...}`, so once a key was saved there was **no way to
replace it from the browser at all**. The rotation was reachable through the
API and nowhere else. The form is now always rendered, with a non-destructive
"Test Without Saving" button and a two-step delete.

**`POST /credentials/test` had been 500ing on every real call.** The response
model declared `equity`/`buying_power` as `Optional[str]`, and the broker
returns a `Decimal`, which Pydantic rejects for a `str` field -
`ResponseValidationError`, HTTP 500. The test that should have caught it mocked
`equity` as the *string* `"10000.00"`, the one thing the SDK never returns. The
frontend was papering over it with `Number(result.equity)`, so the type was a
lie at both ends. See gotcha 22.

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

**A date is also a timezone, and there are two ends to get wrong.**

The backend's `datetime.utcnow()` writes **naive UTC**, which round-trips as a string with no
`Z` and no offset. A browser then reads it as *local* time. On the container's UTC that is
invisible, and in a UTC browser it is invisible to you as the developer - so this is the class
of bug that reproduces for the user and not for you. It is what made 15:30 EDT trades display as
19:30 (gotcha 17).

Fixed at both ends, deliberately:

- **Backend.** `UtcDatetime` in `app/schemas.py` is an `Annotated[datetime, BeforeValidator]`
  applied to every response timestamp. Naive becomes explicitly-UTC *at the serialization
  boundary* - one place - instead of in six call sites that each have to remember and each of
  which fails silently. The API now emits `...Z` and the browser's `new Date()` is correct.
- **Frontend.** `safeFormat(value, pattern, timeZone?)` in `lib/format.ts` renders in a named
  zone, using the Intl wall-clock-shift technique: format the parts as if the instant were UTC,
  then subtract the zone's offset read back off those same shifted parts. Reading the offset
  back off the parts rather than from `Date.getTimezoneOffset()` is what makes half-hour zones
  (`Asia/Kolkata`, `Australia/Adelaide`) correct - a fixed 60-minute shift gets them wrong.

The dashboard passes `marketClock.timezone` - the value the worker actually schedules against -
to every date it draws, and labels the equity chart with it. Same rule as gotcha 16: render what
the API reports, never hardcode "ET" in the frontend.

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

`Decimal` is in the same list and shows up wherever money comes back from the SDK -
`filled_avg_price` and `filled_qty` on every order, `cash`/`buying_power` on every account
snapshot. They bind fine into a SQLite `Float` column, which is what makes them dangerous:
nothing breaks, and you find out later that your price arithmetic is carrying 28 significant
digits. `_fill_from_order()` coerces to `float` explicitly and returns `(None, None)` rather
than `(0.0, 0.0)` for an order with no fill - a zero there is not a missing value, it is a
report that a fill happened for nothing, and it would also mark the row as reconciled so the
reconciler would never look at it again.

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
  either one can escalate itself or take the paper account outright. Every route on the
  `/credentials` router is human-only for this reason, not just the original three - so
  `DELETE /credentials` and `POST /credentials/test-provided` (issue #7) took `require_human()`
  too, and `test_credentials.py` checks all five in the negative direction against a key
  holding `ALL_SCOPES`. A new route here defaults to nothing.

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

**16. The Schedule page's timezone text is served, not hardcoded.**
`GET /bot/config` carries `cron_timezone`, which is `settings.bot_timezone` - the same value the
worker hands to `CronTrigger`. The page used to print "Timezone: UTC", which is true of
APScheduler's default and false of this app, so a `9-16` cron written against the label fired
05:00-12:00 ET: gotcha 8, one layer up, where a user reads it. **Do not "fix" it by hardcoding
"ET" in the frontend** - that is the same bug with a different literal, and it silently becomes
wrong again the first time someone sets `BOT_TIMEZONE` to something else. Render the value the
API reports. The presets are still written as US session times, so the page also warns when the
configured zone is not `America/New_York`.

**17. `market_hours_only` was stored, displayed, and read by nothing - and the reason it
looked like it worked was a timezone bug.** This is the one to read carefully, because the
evidence pointed the wrong way twice.

`is_open` alone gates the cycle, so unchecking "Market Hours Only" changed nothing while the
trades *looked* like they were happening after hours: 19:30-19:50 ET. They were not. Those rows
were stored as **naive UTC**, which is 15:30-15:50 EDT - the last half hour of the regular
session. The bot had never traded after hours; the dashboard was rendering UTC in the browser's
zone and calling it Eastern. So "the flag does nothing" and "the bot trades after hours" were
both the same timestamp bug, and fixing the display is what made the flag's absence visible.

`app/bot/market_hours.py` now decides properly, and the split of authority is the point:

- **The broker's `is_open` is authoritative** for *whether* trading is possible. It knows about
  holidays and half-day closes. A wall clock does not, and a bot that derives "it's 9:30 on a
  Tuesday" from its own clock will happily place an order on Thanksgiving.
- **The wall clock in `MARKET_TZ` only subdivides** that into pre-market / regular /
  after-hours, which is what `market_session_state` reports and what the UI labels.

So `trading_allowed()` is false whenever `market_hours_only` is on and the session is not
regular, and `skip_reason()` returns a sentence saying *which* of the two stopped it - a cycle
that skips with no explanation is indistinguishable from a cycle that is silently broken.
`MarketClockResponse` carries `session_state`, `timezone` and `trading_allowed` so the dashboard
shows the same decision the worker made rather than re-deriving it.

Turning the flag **off** now permits pre-market and after-hours orders, which is the
consequential direction: thin liquidity, poor fills, and a position that can sit overnight. The
UI says so on the control rather than presenting it as a neutral preference.

**18. A read that writes will serialise on SQLite, and the rate limiter will not save you.**
`_authenticate_agent_key` stamps `AgentKey.last_used_at` and commits. That runs on *every*
authenticated agent request, so every read became a write. SQLite's default rollback journal
takes a whole-database lock and fsyncs on commit, and the backend and the worker are separate
containers sharing one file - so the reads serialised, blew past the 5s busy timeout, and
`GET /profiles` returned `500 database is locked`. Measured at 8-way concurrency: one 500 out of
140 agent reads, while the same load with the admin token - which never stamps anything - was
40/40 clean. The tell is that only the *agent* path fails.

The sting is the ordering. Authentication happens **before** `rate_limiter.check`, so a 429 still
took the write lock. The limiter was not a brake on the database at all, and it was the thing
making this reachable: 120 reads/min sustained is exactly the load that triggers it.

Two fixes, and you need both:
- **The stamp is throttled** to `LAST_USED_WRITE_INTERVAL` (60s). `last_used_at` is a
  "when was this key last used" convenience the Agents page renders to the minute, not
  authorisation input, so minute resolution costs nothing. The read path now writes at most once
  a minute per key.
- **WAL plus a 15s busy timeout in `app/db.py`**, via `build_engine()`. WAL is what lets the
  worker's writes proceed while the backend serves reads, instead of the two blocking each other
  by default. `journal_mode` is persistent in the file so it is set once per engine; `busy_timeout`
  is **per-connection**, so it has to be in `connect_args` - setting it on one throwaway
  connection leaves every later connection on the 5s default, which is the failure that actually
  happened.

`conftest.py` no longer sets WAL. It did, and that masked the real state: a test asserting WAL on
the live engine passed for a week while `db.py` set nothing. The test now calls `build_engine()`
against a `tmp_path` file and checks a *second*, freshly-opened connection - that is what proves
`busy_timeout` is in `connect_args` rather than applied once.

Two more things to carry forward: any test counting writes must count real commits (a
SQLAlchemy `commit` event on the engine) rather than assert on `last_used_at`, because a test
that only checks the field changed passes against the old code too - the old code changed it on
every call. And if this ever moves to more than one backend replica, the in-process rate limiter
needs the same treatment described under "Auditing and rate limiting agent actions".

**19. A migration that runs in one of two processes is a crash loop, not a migration.**
There is no Alembic environment - `alembic` sits in requirements.txt and nothing imports it - so
`create_all` + `ensure_schema` is the entire upgrade path. `create_all` creates *tables*, never
columns, which is what `ensure_schema` is for. It was wired into the backend's startup hook
only, and the worker - a separate container, its own engine, its own connection pool, sharing
one SQLite file - never ran it. So deploying `bot_config.capital_allocation_pct` produced a
backend whose `/bot/config` served the new field correctly and a worker that crash-looped on
`no such column: bot_config.capital_allocation_pct`, raising inside `BotWorker.start()` before it
ever scheduled a cycle.

Two things had to be true, and only the first was obvious:

- **Both processes run `init_db()`.** The worker cannot wait for the backend:
  `depends_on: condition: service_started` waits for the container to spawn, *not* for the
  startup hook, and `docker compose restart worker` starts the worker with nothing else running
  at all. Relying on an ordering that `docker compose restart` ignores is relying on nothing.
- **`init_db()` tolerates losing the race.** Two callers means two processes can reach
  `create_all` together, and `create_all(checkfirst=True)` is a read-then-create with no
  `IF NOT EXISTS` behind it. The loser gets `table "x" already exists` and dies on boot, which
  in practice means the worker crash-looping while the backend finishes starting. Retried - and
  **only** for that message. A genuine `OperationalError` (corrupt file, read-only mount, full
  disk) is re-raised immediately, because retrying it turns a clear startup failure into a slow
  one whose message no longer matches the cause.

Two tests earn their keep here. The upgrade test must build a database in the *old* shape,
because a fresh `create_all` already has the column and proves nothing - and it must point the
worker at that file through **both** of its handles, `app.db.get_engine` and the session factory
imported into `worker.scheduler`. Patching only the second leaves the upgrade running against
the shared test database, where the column already exists, and the test passes for the wrong
reason. And every added column carries a `server_default`, because SQLite's `ADD COLUMN` leaves
existing rows NULL - without one, the feature works on new rows and aborts the cycle on the
account you already have.

**20. A `TradeLog` row written without its fill is not a data gap, it is a missing feature.**
`TradeLog.filled_price`/`filled_qty` were NULL on every row in the live database. Capturing the
fill at submit time covers the order that fills immediately; it does not cover the one that
fills *after the cycle closes*, which is the common case for anything but a market order, and
which no cycle will ever look at again because that cycle is over. `_reconcile_fills` runs on a
1-minute job and re-reads unresolved orders through `AlpacaClient.get_order`.

Three things bound it, all of them load-bearing:

- **Recent only** (`RECONCILE_MAX_AGE_HOURS = 48`). Alpaca's order history is a bounded window,
  so an unbounded search re-checks rows whose orders expired weeks ago - a permanent 404 on
  every tick, forever, which fills the log and buries the failures that matter.
- **Not cancelled, rejected or already-filled rows.** A terminal non-fill will never fill;
  reconciling it forever is waste, and `filled_price` staying NULL is the correct record of
  "this never executed".
- **One commit for the sweep, and one failed order contained.** The SQLite write lock from
  gotcha 18 is not something to take once per order, and a single 404 must not take the rest of
  the sweep down. That case is expected rather than exceptional - logged at debug, not error.

Registered **unconditionally**, not under the `is_running` branch: an order placed on the last
cycle before a stop still fills, and a reconciler gated on "the bot is currently running" would
miss exactly the orders most likely to still be in flight.

Check what it finds before assuming. All 52 rows here were `submitted` with no fill, which
reads as "the orders never executed" - and all 52 came back `filled`. They had all executed;
the app had simply never looked. An unfilled column is evidence of *not knowing*, not of a
failure.

**21. A percentage input is in the units its label claims. All of them, always.**
`Max Position Size (%)`, `Max Daily Loss (%)`, `Position Size %` and `Max % of equity per
cycle` were all bound to `min="0.01" max="1"` - fractions, under percent labels. Typing `15`
was refused by the input's own `max`, the box displayed `0.15` where it said 15%, and a user
trying to raise the deployment ceiling would have been fighting the form. Adding a fifth
percentage field on another page that *did* use 1-100 made it worse: two adjacent inputs with
identical labels and opposite scales, which is a more confusing failure than either being
consistently wrong.

The API stores fractions and must keep doing so - every risk limit is a fraction of equity. So
the conversion is a boundary, and there are exactly two: `frac()` in `onSubmit` on the way out,
`pct()` in `handleEdit` on the way in.

The tempting alternative is to leave the form in fractions and convert per input with
react-hook-form's `setValueAs`. **That is wrong**, and quietly: `form.reset()` bypasses
`setValueAs`, so the same field shows a percent after an edit and a fraction after a strategy
change, with no way to tell which is which. Keep the form in the units the user sees.

The related trap is a duplicated schema. `app/schemas.py` and the frontend zod schema each carry
their own defaults, and the zod one won on create - it was at 0.10 position size / 5 positions
while the API defaulted to 0.15 / 10, so **creating a profile without typing anything built a
profile structurally capped at 50% of the account**. Two copies of a default is one default too
many; when you change one, change both.

**22. A response model that lies about a broker type is a 500, and a mock is
what hides it.**

Gotcha 5b says to coerce SDK types explicitly at the route. This is the other
half: the coercion is not enough if the *declared* type was wrong to begin with.
`TestConnectionResponse.equity` was `Optional[str]`, the broker returns a
`Decimal`, and Pydantic v2 rejects a `Decimal` for a `str` field -
`ResponseValidationError` -> HTTP 500, on **every** call. Not intermittent, not
data-dependent: the "Test Connection" button on the setup screen had never
worked.

The reason it survived is the important part. The test mocked
`mock_account.equity = "10000.00"` - a `str` - which is the one type the SDK
never returns. The mock was *more permissive than reality*, so every assertion
passed and the boundary was never crossed. The frontend did not catch it either
because it wrote `Number(result.equity)`, which silently absorbs both a string
and a number: a lie at both ends, each hiding the other.

Two rules that come out of it:

- **Stubs must return the SDK's real types, not convenient ones.** `Decimal` for
  money, `uuid.UUID` for an order id, an `alpaca.trading.enums` member for a
  status. This is the same rule as gotcha 5b applied to the test side, and it
  is the one that actually finds these - a stub that is *more* permissive than
  the real thing is worse than no stub, because it converts a 500 into a green
  test.
- **Check both ends of a boundary, and never let a lenient consumer vouch for a
  strict producer.** `Number(x)` and `String(x)` will absorb a type error from
  either side, so agreement between two wrong ends looks like a passing test.
  Declare the real type on the Pydantic model and the real type in the
  TypeScript interface, and let the compiler and the serializer be the ones that
  disagree.

**23. A sell's size is the position. Never size it against the equity budget.**

All three strategies computed an exit exactly as they computed an entry, and all three
carried a comment saying "flatten position" while doing the opposite:

```python
# app/bot/strategies/rsi_reversion.py:88
elif rsi > overbought:
    # Overbought - SELL (flatten position)
    qty = size_qty(budget, current_price, allow_fractional)   # budget = pct x equity
```

`budget` is what a *fresh* allocation would buy. Applied to closing an old position it is a
number with no relationship to the holding, and the broker refuses the order outright -
`40310000 insufficient qty available`. 22 consecutive rejections on the live account, one per
half-hour cycle, none of which could ever succeed while the holding stayed put.

**The strategies cannot fix this, and that is the finding.** `generate_signals(symbols, params,
alpaca)` receives symbol strings and never learns a holding size, so no correct flatten size
is reachable from inside a strategy. For a sell, the quantity is not a strategy decision at
all. It belongs in `_process_signal`, which had the `Position` objects and dropped `p.qty` one
line after fetching them. It is a `dict[str, float]` now, not a set of symbols - one structure
tracking one fact, because a `set` cannot represent a partial sell and two structures drift the
first time one occurs.

Four things the fix had to get right, each of which is a way to reintroduce it:

- **The clamp is a ceiling, not a substitution.** `min(qty, held)`. A signal smaller than the
  holding closes that much and leaves the rest standing; replacing the quantity outright would
  silently turn a partial exit into a full one.
- **It runs before `validate_order`, not after.** Handing the risk layer a quantity the broker
  was always going to refuse is a check reasoning about a trade that does not exist. The
  position cap is buy-only (key decision 7) so it did not catch this, but any check that *does*
  read sell quantity would have been reasoning about the same fiction.
- **Floor for whole-share profiles, never round up.** Flooring a 0.5-share holding gives 0,
  which is not an order, and is skipped. Rounding up instead is a short sale this code is not
  entitled to open.
- **A partial sell must leave the remainder in the book.** The old code was an unconditional
  `discard`, which is only true of a full close. The book is read by the next signal in the same
  cycle, so a wrong answer there is wrong for the rest of the cycle - and a subtraction residue
  of 1e-16 would keep a closed position in the book and refuse the next buy in that symbol.

**Why it surfaced on 2026-09-29 with no code change: it did not.** The bug is as old as the
strategies. What changed is whether it was masked. With `allow_fractional_shares` off,
`size_qty` floored to whole shares and a whole-share close of a whole-share position happened
to fit. The flag went on, the sell turned fractional, the holding stayed whole, and sizing a
1.00 position from a 12% budget produced 1.62. The first cycle after the switch shows both
halves in the same minute - COST bought 1.29 (fractional live), META sold 1.62 against a 1.00
holding (rejected).

So: **a regression test for this must use a fractional holding or a fractional signal.** Whole
share against whole share passes against the unfixed code, because flooring is exactly what hid
the bug. `test_a_whole_share_profile_floors_the_sell` initially made that mistake and passed
pre-fix; it now asks for 5.0 against a 1.29 holding so the clamp has to be the thing doing the
work.

**It also rots as the account earns.** The budget grows with equity and the holding does not,
so every position bought when equity was lower becomes permanently un-exitable. Three of seven
live positions were already stuck, one by 0.02 shares. That defeats the risk design from
outside the risk layer: `validate_order` deliberately always permits sells so that a breach can
never trap a position, and this made a trapped position reachable anyway. When sizing anything
against equity, ask what the number means for a position that is *closing* rather than opening -
the two have no shared formula.

The invariant worth pinning is not a worked example but the clamp as a choke point:
`test_no_sell_signal_can_over_sell_the_account` sweeps holdings and requested sizes and asserts
`sent <= held` for all of them. That holds for a strategy that does not exist yet, which
removing `qty` from `Signal` would not - a convention in three files is a promise, a property of
the choke point is a guarantee.

## Development Workflow
- **TDD mandatory:** Write failing tests first, then implementation
- **Run tests:** `cd backend && pytest -v` (needs a host Python with the deps)
- **Build:** `docker compose up --build` from project root
- **Typecheck:** `cd frontend && npx tsc --noEmit`
- **Environment:** Copy `.env.example` to `.env`, generate secrets

### Running the tests inside the container
**Never run two pytest processes in the same directory at once.** `conftest.py` points every run
at the same relative file, `sqlite:///./test.db`, and the `recreate_db` autouse fixture drops and
recreates all tables per test - so a second process does not merely slow the first down, it
deletes the tables out from under it. The symptom is a scattering of errors and failures partway
through an otherwise green run, in tests that pass in isolation, and it reads exactly like a real
regression. It happened here while running the suite in the background and then a single file in
the foreground; the background run's own output was the tell (`EEFEF` at 18%). If results look
inexplicable, check for a second pytest before believing them.

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
- **A 429 is a third thing.** It means "this key is over budget", and the
  `detail` names the key. Do not fold it into the 403 handling above, and do not
  teach the axios interceptor to treat it as an expired session.

## Auditing and rate limiting agent actions

Both are in the agent path and both are easy to get subtly wrong in the
direction that makes them look fine.

**`app/audit.py` - three decisions that are load-bearing.** Reads are not
recorded (the dashboard polls, and a table of polls is a table nobody reads);
request bodies are not captured (`record_summary` is opt-in per route, which is
what keeps the Alpaca secret key out of a table any `read` key can fetch back);
and a failed audit write never fails the request, because by then the action has
already been applied - making a logging fault into a trading fault is exactly the
confusion in gotcha 11. The principal comes from `request.state`, which
`get_current_principal` sets. That works because Starlette 0.37 shares one
`scope` dict across the middleware and the endpoint; it would **not** work with
a `ContextVar`, which `BaseHTTPMiddleware` does not propagate back from the task
it runs the endpoint in. A refused call is recorded too - the route never ran,
so nothing downstream logged the attempt, and that 403 is usually the row you
wanted.

**`RateLimiter` in `app/authz.py` - three invariants.** It is charged *after*
authentication and keyed on the resolved **key id**: keyed on the raw token or
the prefix, anyone who knows a prefix could starve a real key by guessing under
it, which turns a read-only limit into a DoS against the human's bot. Windows
are **aligned to the clock**, not opened on a client's first request, or a
caller straddling a boundary gets two budgets. And the **admin session is not
limited at all** - one human in one browser is not the threat model, and a
limiter that can lock the owner out of their own bot is the worse failure.

The limiter is module-level process state, so the test suite resets it in an
autouse fixture in `conftest.py`. That is not tidiness: `recreate_db` drops and
recreates the tables per test, SQLite hands back the same row ids, so key id 1
in one test is a *different key* from key id 1 in the last - but the in-memory
bucket keyed on it is not, and without the reset the suite throttles itself and
the failure looks flaky. **The same caveat is real in production**: this is
exact for one backend replica and wrong behind a load balancer with two. If that
ever happens it has to move to the shared SQLite volume, not gain a second
in-process copy.

**The limiter does not protect the database.** It is charged after
authentication, which is correct for security, but authentication is itself a
write (`last_used_at`), so a rejected request still took the SQLite write lock.
Verifying the limiter by asserting only that a 429 comes back will pass against
a build where every 429 is also a write. Check the write count on the throttled
path - see gotcha 18.

## Verifying the trading path
The trading cycle had never executed. Twelve independent bugs have now been found by actually
running it (four in the cycle itself, then the empty-bars bug, the inert kill-switch, the UTC
schedule, and the order-status/UUID boundary bugs). All are regression-tested, but the lesson
stands: unit tests that mock the Alpaca client cannot see any of this.

The twelfth was found by a different route and is worth describing, because it took a week of
live trading to mature and no amount of mocking would have reached it. **The sell-quantity bug
(gotcha 23) sat in the code from the first release and produced no error at all** until
`allow_fractional_shares` was switched on, and then failed 22 cycles in a row. Replaying the
live profile against real bars was what identified it: `run_strategy` on the real account
returned `SELL META qty=1.63` while the broker held 1.0, which is not a number anyone would
have chosen to write a test around. A second pass drove all seven live positions through the
real `_run_trading_cycle` at the quantity the real strategy computes today, and found three
that could not be closed - one by 0.02 shares. **The useful move was not the one-position
reproduction, it was asking the question of every position rather than the one that was
already shouting.**

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

Since issue #3, patching `get_clock` is no longer sufficient on its own - `is_open=True` is now
necessary but not sufficient, because the cycle also asks `market_session_state` whether the
session is *regular*. Patch it to a regular-hours clock, or you will be testing a cycle that
correctly skips and concluding the market-hours gate is broken.

The same applies to `fake_submit_order` now that fills are captured: it must return
`filled_avg_price` and `filled_qty` as **`Decimal`**, because the real ones are. A stub that
omits them makes the submit-time capture - and the reconciler - look broken for reasons that are
an artefact of the stub.

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
   position. Only `POST /bot/kill-switch` closes positions. This was half-true for a release:
   the daily-loss and kill-switch checks were buy-only, but the position-size cap ran on sells
   too, so a position bought at the cap that then doubled could not be sold by the strategy at
   all - the ordinary way a winner runs up, and exactly what RSI reversion sells into. The cap
   now lives in the buy branch only; `TestsExitsAreNeverGatedByThePositionCap` in
   `test_risk.py` pins both directions.
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
| `/credentials` | `POST /`, `GET /`, `DELETE /`, `GET /status`, `POST /test`, `POST /test-provided` | **human** |
| `/agent-keys` | `GET/POST /`, `GET /scopes`, `DELETE /{id}` | **human** |
| `/profiles` | `GET /`, `GET /{id}` | `read` |
| `/profiles` | `POST /`, `PATCH/DELETE /{id}`, `POST /{id}/activate` | `config:write` |
| `/bot` | `GET /config` | `read` |
| `/bot` | `PATCH /config` | `config:write` |
| `/bot` | `POST /start`, `/stop`, `/pause` | `bot:control` |
| `/bot` | `POST /kill-switch` | `bot:kill` |
| `/dashboard` | `GET /account`, `/positions`, `/orders`, `/equity-curve`, `/logs`, `/market-clock`, `/audit-log` | `read` |
| `/health` | `GET /` (container healthcheck) | public |

`GET /bot/config` returns `capital_allocation_pct` (a fraction, 1.0 = all of it) and
`cron_timezone`. `GET /profiles` returns `max_deployable_pct` per profile -
`min(1, max_concurrent x min(max_position, position_size))`, the ceiling the worker's own caps
impose, computed server-side so the frontend cannot disagree with it. `POST /` and `PATCH /{id}`
reject a profile whose `position_size_pct` exceeds its `risk_max_position_pct` with a 422: the
strategy would order at a size the risk check then refuses, so every order would fail.
`GET /dashboard/market-clock` returns `session_state`, `timezone` and `trading_allowed` - the
worker reads that clock to gate the cycle, so this is its decision, not a second opinion.

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

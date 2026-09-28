<div align="center">

# OpenQuant

**A self-hosted paper-trading bot for Alpaca, with scoped keys so external AI agents can drive it.**

[Paper Trading Only](#paper-trading-only) · [Quick Start](#quick-start) · [Agent API](#agent-api) · [Architecture](#architecture)

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981?logo=open-source-initiative&logoColor=white)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-215%20passing-10b981?logo=pytest&logoColor=white)](#testing)

</div>

---

## Table of Contents

- [What Is This](#what-is-this)
- [Features](#features)
- [Paper Trading Only](#paper-trading-only)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Your First Five Minutes](#your-first-five-minutes)
- [Strategies](#strategies)
- [Risk Management](#risk-management)
- [Agent API](#agent-api)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Commands](#commands)
- [Development](#development)
- [Security](#security)
- [Testing](#testing)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

---

## What Is This

OpenQuant is a Dockerized trading bot that runs strategies against
[Alpaca's paper trading API](https://app.alpaca.markets/paper) on a cron
schedule, with a web dashboard for configuration and monitoring.

The distinguishing feature is the **agent API**. Rather than exposing a
separate surface for automation, an external agent — a monitoring cron, an
LLM assistant, a status page — authenticates against the *same* endpoints the
web UI uses, using a scoped key. You decide whether that key can read, whether
it can reconfigure the bot, or whether it can only read. Every state-changing
call is audited, and refused attempts are recorded too.

**Live trading is impossible by design.** The Alpaca base URL is pinned in code
and ignores any override. This is a research and education tool.

---

## Features

| | |
|---|---|
| **Paper-only by construction** | The client is hardcoded to `paper-api.alpaca.markets` and discards base URL overrides |
| **Three strategies** | SMA crossover, RSI mean reversion, momentum breakout — all parameterized per profile |
| **Scheduled execution** | APScheduler worker on a configurable cron, evaluated in Eastern time |
| **Risk gates** | Position sizing, max concurrent positions, daily loss limit, and an emergency kill switch |
| **Scoped agent keys** | Four scopes, revocable individually, `read`-only by default, per-key rate limits |
| **Audit trail** | Every state-changing call, including refusals, with the record outliving the key |
| **Encrypted credentials** | Alpaca keys encrypted at rest with Fernet (AES-128-GCM) |
| **Live dashboard** | Account, positions, orders, equity curve, trade logs, market clock |
| **Light & dark themes** | Follows the OS by default, no white flash on dark load, manual override |
| **One-command deploy** | `docker compose up --build` |

---

## Paper Trading Only

This is enforced in `backend/app/alpaca_client.py`, not by configuration:

```python
def __init__(self, api_key: str, secret_key: str, base_url: str = None):
    # HARDCODED: paper-only, ignore any passed base_url
    self.base_url = "https://paper-api.alpaca.markets"
    self.trading = TradingClient(api_key, secret_key, paper=True, url_override=self.base_url)
```

The `base_url` argument is accepted and then discarded, and `paper=True` is
passed to the SDK as well. There is no environment variable, request field, or
admin toggle that can reach live trading. If you fork this to trade real money,
you are editing the code — and that is the only way it happens.

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** v2
- An **Alpaca paper trading account** — [get free keys](https://app.alpaca.markets/paper)
- Python 3.9+ on your host *only* if you want to run the two helper commands
  in the next step (any Python with `cryptography` and `bcrypt` will do)

### 1. Clone

```bash
git clone https://github.com/IdoKimhi/openquant.git
cd openquant
```

### 2. Create your `.env`

```bash
cp .env.example .env
```

### 3. Generate the two required secrets

```bash
# Encryption key for the Alpaca credentials (Fernet, 32 bytes)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Bcrypt hash of your login password
python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
```

Paste the first into `SECRET_ENCRYPTION_KEY` and the second into
`APP_PASSWORD_HASH`. The password you pass to `bcrypt` is the one you will type
to log in; the hash is what gets stored.

> **If you are committing changes**, remember to also set a real `JWT_SECRET`.
> The default in `.env.example` is a placeholder.

### 4. Build and run

```bash
docker compose up --build
```

This starts three containers: an nginx/React frontend on port 80, a FastAPI
backend on port 8000, and a scheduler worker. Both backend processes share a
SQLite file on the `bot_data` volume.

### 5. Open the app

Go to **http://localhost** and log in with the password you hashed in step 3.

### 6. Verify the stack

```bash
./test_docker_compose.sh    # asserts all three services, the volume, and both ports
curl http://localhost/api/dashboard/market-clock   # once you have a key
```

---

## Configuration

Everything lives in `.env`. Only two values are required.

| Variable | Required | Default | Purpose |
|---|:--:|---|---|
| `SECRET_ENCRYPTION_KEY` | ✅ | — | Fernet key encrypting the Alpaca API keys at rest |
| `APP_PASSWORD_HASH` | ✅ | — | Bcrypt hash of the admin login password |
| `DATABASE_URL` | | `sqlite:///data/bot.db` | SQLite path on the `bot_data` volume |
| `ALPACA_BASE_URL` | | `https://paper-api.alpaca.markets` | **Ignored at runtime** — paper trading is hardcoded |
| `BOT_SCHEDULE_CRON` | | `*/5 9-16 * * MON-FRI` | Cron expression driving the trading cycle |
| `BOT_TIMEZONE` | | `America/New_York` | Timezone the cron is evaluated in |
| `JWT_SECRET` | | `dev-secret-change-in-production` | Signs admin session tokens — **change it** |
| `JWT_ALGORITHM` | | `HS256` | Token signing algorithm |
| `JWT_EXPIRE_HOURS` | | `24` | Admin session lifetime |
| `AGENT_READ_RATE_LIMIT` | | `120` | Per-minute `GET` budget per agent key |
| `AGENT_WRITE_RATE_LIMIT` | | `20` | Per-minute mutating-request budget per agent key |

### A note on the schedule and timezone

`BOT_SCHEDULE_CRON` is always interpreted in **`BOT_TIMEZONE`**, not UTC. This
matters more than it looks: APScheduler's own default is `Etc/UTC`, under which
the default `9-16` cron fires at **05:00–12:00 ET** and silently skips your
entire trading afternoon. OpenQuant pins the scheduler to Eastern, and the
Schedule page displays the configured zone rather than a hardcoded label — so
if you change `BOT_TIMEZONE`, the UI tells you, and warns you when the
configured zone is not `America/New_York`.

---

## Your First Five Minutes

The app is a five-page wizard, and the order matters.

| Page | What to do |
|---|---|
| **Setup** | Paste your Alpaca **Paper** key ID and secret key, hit **Test Connection**. Stored encrypted. |
| **Strategy** | Create a profile: pick a strategy type, set parameters and risk limits. |
| **Schedule** | Confirm the cron and timezone. |
| **Control** | **Start Bot.** This is the only step that makes anything trade. |
| **Agents** | Optional — mint a scoped key for an external agent. |

Until the bot is started, every cycle short-circuits and the dashboard will
simply look empty.

> **Profile activation has two halves.** The worker selects a profile by
> matching **both** `active_profile_id` and `enabled`. Both fields are written
> together by every activation path in the app, so this stays consistent — but
> if you write code against profiles, resolve them the way the worker does or
> you will build a bug that stops trading with no visible error. See gotcha 12
> in [AGENTS.md](AGENTS.md).

---

## Strategies

Profiles are fully parameterized, so you can run several configurations at once
and activate whichever you want.

### SMA Crossover
| Parameter | Meaning |
|---|---|
| `fast_period` | Fast moving average window |
| `slow_period` | Slow moving average window |

Buys when the fast average crosses above the slow, sells on the inverse cross.

### RSI Mean Reversion
| Parameter | Meaning |
|---|---|
| `period` | RSI lookback window |
| `oversold` | Buy threshold (typically ~30) |
| `overbought` | Sell threshold (typically ~70) |

Buys when RSI falls below `oversold`, sells above `overbought`.

### Momentum Breakout
| Parameter | Meaning |
|---|---|
| `lookback` | Window used to establish the recent high and low |

Buys on a breakout above the recent high, sells on a breakdown below the
recent low.

### Adding a strategy

1. Subclass `BaseStrategy` in `backend/app/bot/strategies/`
2. Register it in `backend/app/bot/strategies/__init__.py` under `STRATEGY_REGISTRY`
3. Add parameter validation in `backend/app/schemas.py`
4. Add tests in `backend/tests/test_strategies.py`

**Build bar requests through `bars_request()`**, the helper on `BaseStrategy`.
Alpaca's bars endpoint returns `200 OK` with an empty payload unless you pass
`start`, and a window that is too narrow returns nothing for a different
reason. Every strategy built its request by hand at first and consequently
never produced a single signal while all tests passed — because the tests
mocked the client to always return bars. See gotcha 6 in [AGENTS.md](AGENTS.md).

---

## Risk Management

`RiskManager` gates orders. It is deliberately a **gate, not a flattener**.

| Control | Behaviour |
|---|---|
| Position size | Caps notional per order |
| Max concurrent positions | Refuses new entries past the cap |
| Daily loss limit | Blocks **new buys** past the threshold |
| Kill switch | `POST /bot/kill-switch` cancels all orders and closes all positions |

When the daily loss limit trips, **sells are always permitted**. A risk gate
that trapped you in a position would be worse than the loss it was preventing.
Only the explicit kill switch flattens the book.

The daily loss figure is computed from the broker's own equity versus the
previous close, not from a local P&L sum — nothing in the app writes P&L to the
database, and a loss limit that silently reads zero is not a limit. See gotcha 7
in [AGENTS.md](AGENTS.md).

---

## Agent API

An external agent drives this instance over the **same HTTP API the web UI
uses**. There is deliberately no parallel `/agent/v1` surface, because a
duplicated router drifts from the original.

Mint a key on the **Agents** page. The plaintext is shown once, at creation.

```bash
curl -H "Authorization: Bearer $OPENQUANT_KEY" http://localhost/api/dashboard/account
curl -H "Authorization: Bearer $OPENQUANT_KEY" http://localhost/api/bot/config
```

Note the base URL is `<origin>/api` — nginx strips that prefix before the
request reaches FastAPI.

### Scopes

Nothing is granted implicitly. A new key gets **`read` and nothing else**.

| Scope | Grants |
|---|---|
| `read` | `GET` on `/dashboard/*`, `/profiles`, `/bot/config` |
| `config:write` | Create, edit, delete, and activate profiles; change the schedule |
| `bot:control` | `POST /bot/start`, `/stop`, `/pause` |
| `bot:kill` | `POST /bot/kill-switch` — cancels all orders, closes all positions |

`bot:kill` is **never** a default under any code path.

`config:write` is not a minor grant. It re-points the bot at a different
strategy — which is exactly how a "read-only" bot ends up trading something
else entirely. Scope it accordingly.

### What an agent can never do

Refused regardless of scopes, with `403`:

- **`/credentials`** — overwrites the Alpaca broker key, so a leaked agent key
  would otherwise be a leaked trading credential.
- **`/agent-keys`** — otherwise an agent could mint itself a wider key and the
  whole scope model would be decorative.

### Status codes worth knowing

| Code | Meaning |
|---|---|
| `401` | Token present but invalid or expired |
| `403` | **Either** no `Authorization` header **or** a valid agent key on a human-only route — the `detail` string distinguishes them |
| `429` | This key is over budget; `Retry-After` header, detail names the key |

### Rate limits

Per key, on clock-aligned fixed windows so a caller straddling a boundary does
not get two budgets.

| Method | Default |
|---|---|
| `GET`, `HEAD`, `OPTIONS` | 120 / min |
| `POST`, `PATCH`, `PUT`, `DELETE` | 20 / min |

Reads are cheap because monitoring is mostly reads; reconfiguration is not.
Tune with `AGENT_READ_RATE_LIMIT` and `AGENT_WRITE_RATE_LIMIT`. The live values
are shown on the Agents page, so an agent can be written against them instead of
discovering them through a `429`.

**Your admin session is not rate limited.** One human in one browser is not the
threat model, and a limiter that can lock the owner out of their own bot is the
worse failure.

### Key handling

Keys are `oq_` + 32 bytes of `token_urlsafe`. Only a **SHA-256 hash** is stored,
so the plaintext is unrecoverable — lose it and you revoke and reissue.
Revocation is **soft**: the row stays visible as evidence the key existed, but
the key authenticates to `None` immediately.

`last_used_at` tells you whether a key is actually in use. It is throttled to
once a minute, so a gap of up to a minute before a key shows as used is
expected, not a fault.

### Audit log

Every state-changing call is recorded: who, which key, what, and the status
code. Reads are not — the dashboard polls, and a table of polls is a table
nobody reads.

```bash
curl -H "Authorization: Bearer $OPENQUANT_KEY" \
  "http://localhost/api/dashboard/audit-log?limit=50"
curl -H "Authorization: Bearer $OPENQUANT_KEY" \
  "http://localhost/api/dashboard/audit-log?actor=agent"
```

Two properties worth having:

- **A refused attempt is a row.** A key lacking `bot:kill` gets a `403` *and* an
  audit entry. The route never ran, so nothing downstream would have logged it
  — and that is usually the attempt you want to see.
- **The record outlives the key.** Revoking a key stops it working; it does not
  erase what it did.

**Request bodies are never captured.** A route that wants detail calls
`audit.record_summary(request, ...)` with what it actually changed. This is
what keeps your Alpaca secret key out of a table that any `read` key can fetch
back.

---

## Architecture

```
                    ┌──────────────────────────────┐
   Browser ────────▶│  frontend   nginx + React    │  :80
                    │  strips /api, proxies rest   │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  backend    FastAPI          │  :8000
                    │  auth · routes · dashboard   │
                    └──────────────┬───────────────┘
                                   │  bot_data volume
                    ┌──────────────▼───────────────┐
                    │  worker     APScheduler      │
                    │  trading cycle, risk gate    │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Alpaca  paper-api only      │
                    └──────────────────────────────┘
```

- **One SQLite file** on the `bot_data` named volume, shared by backend and
  worker. WAL mode with a 15s busy timeout, set per-connection in
  `connect_args` — see gotcha 18 in [AGENTS.md](AGENTS.md) for why that
  distinction is load-bearing.
- **Two principals, one endpoint set.** The admin logs in with a password and
  receives a JWT; an agent presents an `oq_` scoped key. Both are resolved to
  the same `Principal` object and hit the same routes, gated per-route by
  `require_scope` or `require_human`.
- **The worker runs no HTTP server**, so its inherited Docker `HEALTHCHECK` is
  explicitly disabled in `docker-compose.yml` — otherwise a perfectly healthy
  scheduler reports as unhealthy forever.

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Project Structure

```
openquant/
├── docker-compose.yml        # frontend, backend, worker, bot_data volume
├── .env.example              # every supported variable, documented
├── AGENTS.md                 # gotchas that have actually caused bugs
├── LICENSE
├── test_docker_compose.sh    # asserts the stack shape
├── docs/
│   └── ARCHITECTURE.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # FastAPI app, health, router registration
│   │   ├── config.py         # Pydantic Settings
│   │   ├── security.py       # Fernet, JWT, bcrypt   (authentication)
│   │   ├── authz.py          # Principal, scopes, keys, RateLimiter  (authorization)
│   │   ├── audit.py          # AuditMiddleware, record_summary
│   │   ├── db.py             # SQLAlchemy engine, WAL, busy timeout
│   │   ├── models.py
│   │   ├── alpaca_client.py  # paper-only broker wrapper
│   │   ├── schemas.py
│   │   ├── bot/
│   │   │   ├── engine.py     # strategy execution
│   │   │   ├── risk.py       # sizing, daily loss, kill switch
│   │   │   └── strategies/   # base + 3 strategies + registry
│   │   └── routes/           # auth, credentials, profiles, bot_config,
│   │                         #   dashboard, agent_keys
│   ├── tests/                # 18 files, 215 tests
│   └── worker/
│       ├── main.py
│       └── scheduler.py      # APScheduler jobs, trading cycle
└── frontend/
    ├── nginx.conf            # strips /api, proxies to backend
    ├── index.html            # blocking pre-paint theme script
    ├── tailwind.config.js    # darkMode: 'class', semantic colour tokens
    └── src/
        ├── api/              # one module per router
        ├── components/       # Layout, Sidebar, ThemeToggle
        ├── hooks/            # useAuth, useTheme
        ├── lib/              # format.ts, chartColors.ts
        └── pages/            # Setup, Strategy, Schedule, Control, Dashboard, Agents
```

---

## API Reference

The base URL an agent uses is `<origin>/api`. "Human" means an admin session
only — an agent key is refused.

### Public

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Container healthcheck |
| `POST` | `/auth/login` | Password → JWT |
| `GET` | `/auth/verify` | Validate a token |

### Human only

| Method | Path | Notes |
|---|---|---|
| `POST` | `/credentials` | Store encrypted Alpaca keys |
| `GET` | `/credentials/status` | Whether keys exist |
| `POST` | `/credentials/test` | Test the broker connection |
| `GET` | `/agent-keys` | List keys — prefixes and scopes, never the key |
| `POST` | `/agent-keys` | Create a key; plaintext is in this response only |
| `GET` | `/agent-keys/scopes` | The scope catalogue with descriptions |
| `DELETE` | `/agent-keys/{id}` | Revoke a key (soft delete) |

### Scoped

| Method | Path | Scope |
|---|---|---|
| `GET` | `/profiles`, `/profiles/{id}` | `read` |
| `POST` | `/profiles` | `config:write` |
| `PATCH` `DELETE` | `/profiles/{id}` | `config:write` |
| `POST` | `/profiles/{id}/activate` | `config:write` |
| `GET` | `/bot/config` | `read` |
| `PATCH` | `/bot/config` | `config:write` |
| `POST` | `/bot/start`, `/stop`, `/pause` | `bot:control` |
| `POST` | `/bot/kill-switch` | `bot:kill` |
| `GET` | `/dashboard/account` | `read` |
| `GET` | `/dashboard/positions` | `read` |
| `GET` | `/dashboard/orders` | `read` |
| `GET` | `/dashboard/equity-curve` | `read` |
| `GET` | `/dashboard/logs` | `read` |
| `GET` | `/dashboard/market-clock` | `read` |
| `GET` | `/dashboard/audit-log` | `read` |

**There is no agent-facing "place an order" endpoint.** The action surface is
start/stop/pause, kill switch, profile and schedule changes, and reads. Direct
order placement would be a different trust boundary from configuring a bot that
places orders for itself.

---

## Commands

### Day-to-day

```bash
docker compose up --build            # build and start everything
docker compose up -d                 # start in the background
docker compose logs -f worker        # follow the trading cycle
docker compose logs -f backend
docker compose ps                    # service states
docker compose restart backend       # restart one service
docker compose down                  # stop, keep the data volume
docker compose down -v               # stop and DELETE the database
```

### Testing

```bash
cd backend && pytest -v                                        # 215 tests
cd frontend && npx tsc --noEmit                                # typecheck
cd frontend && npm run build                                   # production build
./test_docker_compose.sh                                       # stack shape
```

### Running the tests in the container

The image ships `app/` and `worker/`, not `tests/`, and `/app` is root-owned
while the container runs as `appuser`. Run from a writable directory:

```bash
C=$(docker compose ps -q backend)
docker exec -u root "$C" rm -rf /app/tests          # docker cp nests an existing dir
docker cp backend/tests "$C":/app/tests
docker exec -u root "$C" chown -R appuser:appuser /app/tests
docker exec "$C" sh -c 'cd /tmp && python -m pytest /app/tests -q'
```

> The `rm -rf` is not optional. `docker cp src dst` treats an existing `dst`
> directory as a *parent* and creates `dst/src_basename`, so a stale copy
> silently keeps running the old tests.

### Secret generation

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
```

---

## Development

```bash
# Backend — hot reload
cd backend
python -m uvicorn app.main:app --reload --port 8000

# Frontend — Vite dev server
cd frontend
npm install
npm run dev
```

### Adding a route

Every route declares what it needs. **A route with no dependency is public** —
there is no implicit protection.

```python
@router.post("/kill-switch")
async def kill_switch(user=Depends(require_scope(SCOPE_BOT_KILL))): ...

@router.post("/credentials")
def store_credentials(data: CredentialsIn, user=Depends(require_human()): ...
```

### Theming

All colour resolves to a CSS custom property declared twice in
`frontend/src/index.css`, once under `:root` and once under `.dark`, and
exposed through `tailwind.config.js` as semantic roles: `bg-surface`,
`text-body`, `text-muted`, `text-subtle`, `border-line`, `bg-accent-soft`,
`bg-danger-soft`, `border-line-danger`.

Use roles, not values. A literal `text-gray-900` is a light-theme-only decision
that dark mode cannot reach. The `dark:` variant is deliberately unused
throughout — if you find yourself writing `dark:bg-slate-800`, the component is
missing a semantic token instead.

Three traps worth knowing: `-fg` and `-contrast` are different colours
(a pale fill takes `-fg`, a solid button takes `-contrast`); `border-line-<c>`
and `border-<c>` are not interchangeable; and Recharts writes literal colours
into SVG attributes, so charts must read the `--chart-*` variables through
`useChartColors()`.

---

## Security

- **Paper endpoint hardcoded.** No configuration path reaches live trading.
- **Credentials encrypted at rest** with Fernet (AES-128-GCM), key from
  `SECRET_ENCRYPTION_KEY`.
- **Admin auth** via bcrypt password hash → JWT.
- **Agent keys** stored as SHA-256 hashes, shown once, individually revocable,
  `read`-only by default, and `bot:kill` never granted implicitly.
- **Every state-changing call audited**, including refusals, and the trail
  outlives the key that wrote it.
- **Request bodies never logged**, so credential material cannot reach the
  audit table — which any `read` key can query.
- **Per-key rate limits**; the admin session is exempt. A throttled request
  performs no database write, so a rate-limited agent cannot lock the trading
  cycle out of its own database.
- **`/credentials` and `/agent-keys` unreachable** by any agent key.
- **No secrets in logs or source.**

---

## Testing

215 tests across 18 files, all passing.

```bash
cd backend && pytest -v
```

### The lesson this suite encodes

[AGENTS.md](AGENTS.md) documents **eleven independent bugs that shipped while
every unit test passed**, concentrated in the trading path. The trading cycle
had never actually executed. The causes were instructive:

- `worker/scheduler.py` had **no tests at all**.
- The strategy tests **mocked the Alpaca client to always return bars**, so a
  request that returned nothing in production looked fine.
- A test asserted on `StrategyProfile.enabled` when the worker selects on
  `active_profile_id` **and** `enabled` — asserting on precisely the field that
  was wrong. It passed for a full release.

So: **do not add a feature to the trading path without a check that runs
against real market data, and do not trust a mock that returns convenient
types.** A stub for `submit_order` must return a `uuid.UUID` id and a real
`alpaca.trading.enums.OrderStatus` member, because a stub returning plain
strings is exactly what hid bugs 7–9. `get_clock` must be patched open or the
cycle short-circuits on a weekend and you conclude there is nothing to see.
And rows must be read back through the ORM, because a commit that succeeds can
still have written a value the ORM cannot load.

[AGENTS.md](AGENTS.md) is the operational manual for all of this, including
which failures are silent and why.

---

## Known Limitations

Stated plainly, because each of these is a real thing that will otherwise
surprise you:

- **`market_hours_only` is stored and displayed but never read.** The worker
  gates every cycle on Alpaca's market clock unconditionally, so unchecking
  "Market Hours Only" does nothing. The UI says so rather than implying
  extended-hours trading exists. Wiring it up would let the bot place pre-market
  and after-hours orders, so it needs its own verification before anyone enables
  it.
- **The rate limiter is per-process.** Exact for the single-backend deployment
  this ships with; wrong behind a load balancer with two replicas. Moving to
  multiple replicas means moving the counters to the shared volume, not adding a
  second in-process copy.
- **One active profile at a time.** The schedule is global, not per-profile.
- **SQLite, by design.** Fine for one bot; not a multi-tenant design.
- **The API has no idempotency keys**, so a retried `POST` is a second order.
- **The frontend hand-writes its TypeScript interfaces** alongside the Pydantic
  response models, and they can drift silently — a missing field usually
  renders as `undefined` rather than throwing, and a missing *date* field used
  to blank the entire dashboard page. See gotcha 3 in [AGENTS.md](AGENTS.md).

---

## Contributing

Contributions are welcome, particularly new strategies.

1. Open an issue describing the strategy or fix.
2. Branch from `master`.
3. If you touch the trading path, add a test that drives real code — the bar
   is described in [AGENTS.md](AGENTS.md) under "Verifying the trading path".
4. Run the suite and the typecheck.
5. Open a PR describing what you changed **and how you verified it**.

Please read [AGENTS.md](AGENTS.md) before your first contribution. It is a list
of traps that have each cost a real bug, and several are counter-intuitive —
for instance, `TradeLog.status` is a `String` column rather than an enum
*despite* a local enum existing, because Alpaca sends 18 status values and
there is no CHECK constraint in SQLite to stop a bad one from being written.

---

## License

[MIT](LICENSE) © 2026 IdoKimhi

> Trading involves substantial risk of loss. This software is provided as-is,
> with no warranty of any kind. It is restricted to paper trading and is
> intended for research and education. You are responsible for anything you do
> with it.

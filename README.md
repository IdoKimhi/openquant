# OpenQuant

A Dockerized, self-hosted paper-trading application for Alpaca's paper trading API, with a
scoped key system so external AI agents can drive it. Built with FastAPI, React, and APScheduler.

## Features

- **Paper Trading Only** - Connects exclusively to Alpaca's paper trading endpoint
- **Encrypted Credentials** - API keys encrypted at rest using Fernet (AES-128-GCM)
- **Strategy Profiles** - Configure and manage multiple trading strategies (SMA Crossover, RSI Mean Reversion, Momentum Breakout)
- **Scheduled Execution** - APScheduler worker runs on configurable cron schedule during market hours
- **Risk Management** - Position size limits, daily loss kill-switch, max concurrent positions
- **Live Dashboard** - Real-time account data, positions, orders, equity curve, and bot activity logs
- **Light & Dark Themes** - Follows your OS setting by default, with a toggle in the header
- **Agent API** - Scoped, individually revocable keys let an external agent read state and
  control the bot, with least privilege as the default
- **Single Command Deploy** - `docker compose up --build`

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Alpaca Paper Trading API keys (get them at https://app.alpaca.markets/paper)

### Setup

1. Clone and navigate to the project:
   ```bash
   cd /opt/alpaca-paper-bot
   ```

2. Copy the example environment file and configure:
   ```bash
   cp .env.example .env
   ```

3. Generate required secrets:
   ```bash
   # Encryption key for API credentials
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   
   # Password hash for login
   python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
   ```

4. Fill in `.env` with the generated values.

5. Start the application:
   ```bash
   docker compose up --build
   ```

6. Access the GUI at http://localhost

### Configuration

1. **Setup Page** - Enter your Alpaca Paper API Key ID and Secret Key, click "Test Connection"
2. **Strategy Page** - Create a strategy profile (select type, configure parameters, set risk limits)
3. **Schedule Page** - Configure the cron schedule (default: every 5 minutes during market hours)
4. **Control Page** - Click "Start Bot" to begin trading
5. **Agents Page** - Optionally create a scoped API key for an external agent (see below)

## Agent API

An external agent (a monitoring job, an LLM-driven assistant, a dashboard) can act on this
instance over the same HTTP API the web UI uses. There is no separate agent-specific surface.

Go to the **Agents** page and create a key. The page shows the base URL and the key itself, once.

```
curl -H "Authorization: Bearer $OPENQUANT_KEY" http://localhost/api/dashboard/account
curl -H "Authorization: Bearer $OPENQUANT_KEY" http://localhost/api/bot/config
```

### Scopes

Every key carries an explicit scope list. Nothing is granted implicitly.

| Scope | Grants |
|-------|--------|
| `read` | `GET` on `/dashboard/*`, `/profiles`, `/bot/config` |
| `config:write` | Create/edit/delete/activate strategy profiles, change the schedule |
| `bot:control` | `POST /bot/start`, `/stop`, `/pause` |
| `bot:kill` | `POST /bot/kill-switch` - cancels all orders and closes all positions |

A new key gets **`read` and nothing else**. `bot:kill` is never a default, and note that
`config:write` is not a minor grant: it re-points the bot at a different strategy, which is
how a "read-only" bot ends up trading something else entirely.

### Two things an agent can never do
Regardless of scopes, an agent key is refused by:

- `/credentials` - this overwrites the Alpaca broker key. A leaked agent key would otherwise
  become a leaked trading credential.
- `/agent-keys` - otherwise an agent could mint itself a wider key and the scope model would be
  decorative.

### Key handling

Keys look like `oq_<43 chars>`. Only a SHA-256 hash is stored, so the plaintext is shown once at
creation and cannot be recovered - if you lose it, revoke the key and create another. Revoking
is immediate and takes effect on the next request; the key stays listed, marked revoked, so you
can still see it existed. `last_used_at` tells you whether a key is actually in use.

The admin session is a separate principal: an agent key presented to a human-only route gets
`403`, and a missing `Authorization` header also gets `403` (not `401`) - the detail string is
what distinguishes them.

## Themes

Light and dark, toggled from the header. On a first visit the app follows your OS
`prefers-color-scheme`; once you use the toggle your choice is remembered and the OS is
ignored. It is stored in `localStorage` under `openquant.theme`.

No white flash on a dark OS: a small blocking script in `index.html` sets the theme class
before the first paint.

If you extend the UI, add colours as *roles*, not values - `bg-surface`, `text-body`,
`text-muted`, `text-subtle`, `border-line`, `bg-accent-soft`, `bg-danger-soft`,
`border-line-danger`. Each is defined once for light and once for dark in
`frontend/src/index.css`. A literal `text-gray-900` is a light-theme-only decision that dark
mode cannot reach, and the `dark:` variant is deliberately unused throughout.

## Project Structure

```
openquant/
├── docker-compose.yml
├── .env.example
├── .env (gitignored)
├── README.md
├── docs/
│   └── ARCHITECTURE.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── security.py            # Fernet, JWT, bcrypt (authentication)
│   │   ├── authz.py               # Principal, scopes, agent key generation (authorization)
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── alpaca_client.py
│   │   ├── bot/
│   │   │   ├── engine.py
│   │   │   ├── strategies/
│   │   │   │   ├── base.py
│   │   │   │   ├── sma_crossover.py
│   │   │   │   ├── rsi_reversion.py
│   │   │   │   └── momentum_breakout.py
│   │   │   ├── risk.py
│   │   │   └── __init__.py
│   │   └── routes/
│   │       ├── auth.py
│   │       ├── credentials.py
│   │       ├── profiles.py
│   │       ├── bot_config.py
│   │       ├── dashboard.py
│   │       └── agent_keys.py
│   ├── tests/
│   └── worker/
│       ├── main.py
│       └── scheduler.py
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── nginx.conf
│   ├── index.html                 # pre-paint theme script
│   ├── tailwind.config.js         # darkMode: 'class' + semantic colour tokens
│   └── src/
│       ├── index.css              # :root / .dark token blocks
│       ├── pages/
│       ├── components/
│       ├── hooks/
│       ├── lib/
│       └── api/
└── .gitignore
```

## API Endpoints

### Auth
- `POST /auth/login` - Login with password
- `GET /auth/verify` - Verify token

### Agent Keys (human session only)
- `GET /api/agent-keys` - List keys (prefixes and scopes, never the key itself)
- `POST /api/agent-keys` - Create a key; the plaintext is in the response and only there
- `GET /api/agent-keys/scopes` - The scope catalogue, with descriptions and defaults
- `DELETE /api/agent-keys/{id}` - Revoke a key

### Credentials (human session only)
- `POST /api/credentials` - Store encrypted API keys
- `GET /api/credentials/status` - Check if keys exist
- `POST /api/credentials/test` - Test Alpaca connection

### Strategy Profiles
- `GET /api/profiles` - List profiles (`read`)
- `POST /api/profiles` - Create profile (`config:write`)
- `PATCH /api/profiles/{id}` - Update profile (`config:write`)
- `DELETE /api/profiles/{id}` - Delete profile (`config:write`)
- `POST /api/profiles/{id}/activate` - Set as active (`config:write`)

### Bot Config
- `GET /api/bot/config` - Get config (`read`)
- `PATCH /api/bot/config` - Update config (`config:write`)
- `POST /api/bot/start` - Start bot (`bot:control`)
- `POST /api/bot/stop` - Stop bot (`bot:control`)
- `POST /api/bot/pause` - Pause bot (`bot:control`)
- `POST /api/bot/kill-switch` - Emergency flatten all positions (`bot:kill`)

### Dashboard (all `read`)
- `GET /api/dashboard/account` - Account summary
- `GET /api/dashboard/positions` - Open positions
- `GET /api/dashboard/orders` - Order history
- `GET /api/dashboard/equity-curve` - Equity time series
- `GET /api/dashboard/logs` - Trade logs
- `GET /api/dashboard/market-clock` - Market status

## Strategies

### SMA Crossover
- Fast/Slow period moving averages
- Buy when fast crosses above slow, sell when crosses below

### RSI Mean Reversion
- RSI period, oversold/overbought thresholds
- Buy when RSI < oversold, sell when RSI > overbought

### Momentum Breakout
- Lookback period for high/low
- Buy on breakout above recent high, sell on breakdown below recent low

## Security

- All API keys encrypted at rest with Fernet (AES-128-GCM)
- Encryption key derived from `SECRET_ENCRYPTION_KEY` environment variable
- JWT-based authentication with bcrypt password hashing
- Agent keys stored as SHA-256 hashes, shown once, individually revocable, read-only by default
- `/credentials` and `/agent-keys` are unreachable by an agent key
- Paper trading endpoint hardcoded - no live trading possible
- No secrets in logs or source code

## Testing

```bash
# Run backend tests
cd /opt/alpaca-paper-bot/backend && pytest -v

# Typecheck frontend
cd /opt/alpaca-paper-bot/frontend && npx tsc --noEmit

# Run frontend build
cd /opt/alpaca-paper-bot/frontend && npm run build
```

## Development

### Adding a New Strategy

1. Create strategy class in `backend/app/bot/strategies/` extending `BaseStrategy`
2. Register in `backend/app/bot/strategies/__init__.py`
3. Add parameter validation in `backend/app/schemas.py`
4. Add tests in `backend/tests/test_strategies.py`

### Running Locally

```bash
# Backend
cd backend
python -m uvicorn app.main:app --reload

# Frontend
cd frontend
npm run dev
```

## License

MIT
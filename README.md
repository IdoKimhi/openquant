# Alpaca Paper Trading Bot

A Dockerized, self-hosted paper-trading application for Alpaca's paper trading API. Built with FastAPI, React, and APScheduler.

## Features

- **Paper Trading Only** - Connects exclusively to Alpaca's paper trading endpoint
- **Encrypted Credentials** - API keys encrypted at rest using Fernet (AES-128-GCM)
- **Strategy Profiles** - Configure and manage multiple trading strategies (SMA Crossover, RSI Mean Reversion, Momentum Breakout)
- **Scheduled Execution** - APScheduler worker runs on configurable cron schedule during market hours
- **Risk Management** - Position size limits, daily loss kill-switch, max concurrent positions
- **Live Dashboard** - Real-time account data, positions, orders, equity curve, and bot activity logs
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

## Project Structure

```
alpaca-paper-bot/
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
│   │   ├── security.py
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
│   │       └── market.py
│   ├── tests/
│   └── worker/
│       ├── main.py
│       └── scheduler.py
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── nginx.conf
│   └── src/
│       ├── pages/
│       ├── components/
│       ├── hooks/
│       └── api/
└── .gitignore
```

## API Endpoints

### Auth
- `POST /auth/login` - Login with password
- `GET /auth/verify` - Verify token

### Credentials
- `POST /api/credentials` - Store encrypted API keys
- `GET /api/credentials/status` - Check if keys exist
- `POST /api/credentials/test` - Test Alpaca connection

### Strategy Profiles
- `GET /api/profiles` - List profiles
- `POST /api/profiles` - Create profile
- `PATCH /api/profiles/{id}` - Update profile
- `POST /api/profiles/{id}/activate` - Set as active

### Bot Config
- `GET /api/bot/config` - Get config
- `PATCH /api/bot/config` - Update config
- `POST /api/bot/start` - Start bot
- `POST /api/bot/stop` - Stop bot
- `POST /api/bot/kill-switch` - Emergency flatten all positions

### Dashboard
- `GET /api/account` - Account summary
- `GET /api/positions` - Open positions
- `GET /api/orders` - Order history
- `GET /api/equity-curve` - Equity time series
- `GET /api/logs` - Trade logs
- `GET /api/market-clock` - Market status

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
- Paper trading endpoint hardcoded - no live trading possible
- No secrets in logs or source code

## Testing

```bash
# Run backend tests
cd /opt/alpaca-paper-bot/backend && pytest -v

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
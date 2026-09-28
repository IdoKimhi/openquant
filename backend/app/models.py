from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, ForeignKey, Enum as SQLEnum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base
import enum


class StrategyType(str, enum.Enum):
    sma_crossover = "sma_crossover"
    rsi_reversion = "rsi_reversion"
    momentum_breakout = "momentum_breakout"


class OrderSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, enum.Enum):
    market = "market"
    limit = "limit"


class OrderStatus(str, enum.Enum):
    submitted = "submitted"
    filled = "filled"
    canceled = "canceled"
    rejected = "rejected"
    error = "error"


class ApiCredentials(Base):
    __tablename__ = "api_credentials"

    id = Column(Integer, primary_key=True, index=True)
    key_id_encrypted = Column(String, nullable=False)
    secret_key_encrypted = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategyProfile(Base):
    __tablename__ = "strategy_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    strategy_type = Column(SQLEnum(StrategyType), nullable=False)
    parameters = Column(JSON, nullable=False)
    # Defaults deploy the whole account. They used to be 0.10 x 5, which caps
    # the profile at 50% of equity by construction, and 0.10 x 15% - the live
    # RSI profile - at 75%: a quarter of the account was unreachable and no
    # amount of waiting for signals would change it. That is issue #2's idle
    # cash, and the arithmetic is now visible in `max_deployable_pct` rather
    # than being something to be surprised by.
    risk_max_position_pct = Column(Float, default=0.15, server_default="0.15")
    risk_max_daily_loss_pct = Column(Float, default=0.05)
    risk_max_concurrent_positions = Column(Integer, default=10, server_default="10")
    # Fractional quantities, so the remainder of a position is deployed instead
    # of discarded. Opt-in per profile because Alpaca only accepts a fractional
    # qty on a *market* order; see size_qty() in app/bot/risk.py. Default off,
    # so a profile that has never considered it keeps trading whole shares.
    allow_fractional_shares = Column(Boolean, default=False, server_default="0")
    symbols = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def position_size_pct(self) -> float:
        """The strategy's own target size, as a fraction of investable equity.

        Read from `parameters` because that is where the strategies read it
        from, and two sources for one number is how a profile ends up refusing
        its own orders (see `max_deployable_pct`).
        """
        return float((self.parameters or {}).get("position_size_pct", 0.0) or 0.0)

    @property
    def max_deployable_pct(self) -> float:
        """The most of the account this profile can put to work, as a fraction.

            min(1, concurrency_cap x min(per_position_cap, position_size_pct))

        Both caps bound the same thing from opposite ends, so the smaller one
        governs: a 5-position book cannot deploy more than five positions'
        worth, and no single position may exceed the per-position cap.

        `position_size_pct` is the third input and it is the one that used to
        be invisible. The strategies size orders with it, but
        `check_position_size` refuses anything above `risk_max_position_pct` -
        two independently editable numbers with no relationship between them.
        Setting the first to 25% while the second stays at 10% means every
        order is rejected with "Position size 25.00% exceeds max 10.00%" and
        the cash sits idle, which reads as a broken bot rather than a
        contradiction between two fields. POST /profiles now rejects that
        combination; this property reports it for profiles that predate the
        check.
        """
        position_cap = min(
            max(float(self.risk_max_position_pct or 0.0), 0.0),
            max(self.position_size_pct, 0.0),
        )
        positions = max(int(self.risk_max_concurrent_positions or 0), 0)
        return min(1.0, round(positions * position_cap, 6))

    @property
    def cash_sweep_enabled(self) -> bool:
        return bool((self.parameters or {}).get("cash_sweep", {}).get("enabled", False))

    @property
    def cash_sweep_symbol(self) -> str | None:
        value = (self.parameters or {}).get("cash_sweep", {}).get("symbol")
        return str(value).strip().upper() if value else None

    @property
    def cash_sweep_pct(self) -> float:
        """Ceiling on a single sweep, as a fraction of investable equity.

        A sweep that could reach for everything spare would open with whatever
        cash accumulated over a quiet week. Bounded per cycle instead, and still
        inside the per-position cap, so it cannot quietly become a larger
        position than the profile's own limit allows.
        """
        return float((self.parameters or {}).get("cash_sweep", {}).get("pct", 0.0) or 0.0)


class AgentKey(Base):
    """A scoped credential for an external agent.

    Separate from the admin password on purpose. The admin credential is a
    single shared secret that mints one JWT shape for every route, which is
    fine for one human on one browser but useless for a machine caller: an
    agent's actions are untraceable, it cannot be revoked without locking the
    human out, and it has all-or-nothing access to the kill switch.

    Only the hash of the key is stored. `key_prefix` is the lookup handle and
    is safe to render in the UI; `key_hash` is sha256 of the full plaintext and
    is the only thing that can authenticate.
    """
    __tablename__ = "agent_keys"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=False)
    key_prefix = Column(String, nullable=False, index=True)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    scopes = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class AuditLog(Base):
    """One row per state-changing API call, whoever made it.

    `AgentKey.last_used_at` cannot answer "did an agent start the bot?" - it
    only records that a key was presented - and it stops recording the moment
    the key is revoked, which is exactly when the question gets asked. This is
    the record that survives revocation.

    Deliberately narrow. Reads are not recorded (the dashboard polls on an
    interval, and burying actions under noise makes the trail useless), and
    request bodies are not captured: `detail` is written only by a route that
    opts in via `audit.record_summary`, so a body-capturing trail cannot end
    up writing the Alpaca secret key to a table the `read` scope can read.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # "user" | "agent" | "anonymous". Anonymous is not a third kind of caller
    # so much as "we could not attribute this" - a missing header, or a token
    # that failed to authenticate. A refused or unauthenticated attempt is
    # still worth a row, since the route never ran and logged nothing itself.
    actor_kind = Column(String, nullable=False)
    actor_label = Column(String, nullable=True)
    key_id = Column(Integer, nullable=True)
    method = Column(String, nullable=False)
    path = Column(String, nullable=False)
    # Route template ("/profiles/{profile_id}/activate"). `path` alone would
    # be a distinct value per profile, which makes grouping impossible.
    action = Column(String, nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    detail = Column(Text, nullable=True)
    client_ip = Column(String, nullable=True)


class BotConfig(Base):
    __tablename__ = "bot_config"

    id = Column(Integer, primary_key=True, index=True)
    schedule_cron = Column(String, default="*/5 9-16 * * MON-FRI")
    market_hours_only = Column(Boolean, default=True)
    active_profile_id = Column(Integer, ForeignKey("strategy_profiles.id"), nullable=True)
    is_running = Column(Boolean, default=False)
    # Share of account equity the strategies may size positions against
    # (issue #6, "money to invest"). Defaults to the whole account so an
    # existing deployment behaves exactly as it did before the column
    # existed - the SQLite column default below is what backfills existing
    # rows, and there is no separate data migration to forget to run.
    capital_allocation_pct = Column(Float, default=1.0, server_default="1.0")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    active_profile = relationship("StrategyProfile")


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    profile_id = Column(Integer, ForeignKey("strategy_profiles.id"), nullable=True)
    symbol = Column(String, nullable=False)
    side = Column(SQLEnum(OrderSide), nullable=False)
    qty = Column(Float, nullable=False)
    order_type = Column(SQLEnum(OrderType), default=OrderType.market)
    limit_price = Column(Float, nullable=True)
    # Free text, NOT SQLEnum(OrderStatus). This used to be SQLEnum, which
    # looked safer but protected nothing: SQLAlchemy 2.0 emits no CHECK
    # constraint by default, so the worker writing Alpaca's raw status
    # ('accepted', 'partially_filled', ...) committed fine and the row then
    # could not be loaded back - the ORM raises LookupError coercing an unknown
    # value into the enum, which 500'd GET /dashboard/logs on the first trade.
    # The worker now maps through local_order_status() before writing, and a
    # String column means a stale row can never break the read path again.
    status = Column(String, nullable=True, default=OrderStatus.submitted.value)
    alpaca_order_id = Column(String, nullable=True)
    filled_price = Column(Float, nullable=True)
    filled_qty = Column(Float, nullable=True)
    message = Column(Text, nullable=False)
    error_details = Column(Text, nullable=True)
    pnl = Column(Float, nullable=True)  # Profit/Loss for filled trades

    profile = relationship("StrategyProfile")


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    equity = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    buying_power = Column(Float, nullable=False)
    day_pl = Column(Float, nullable=False)
    total_pl = Column(Float, nullable=False)
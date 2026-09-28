from pydantic import (
    BaseModel, BeforeValidator, Field, field_validator, model_validator,
)
from typing import Annotated, Optional, List
from datetime import datetime, timezone
from app.authz import ALL_SCOPES, DEFAULT_SCOPES
from app.models import StrategyType, OrderSide, OrderType, OrderStatus


def _to_utc(value):
    """Pin a datetime to an explicit instant.

    Every timestamp column here is written with `default=datetime.utcnow`,
    which yields a *naive* datetime holding UTC wall-clock fields. Serialised
    that is "2026-09-28T19:50:03.279066" - no offset, no Z - and the
    ECMAScript spec reads a date-time without an offset as **local** time.
    `new Date("2026-09-28T19:50:03.279066")` in a UTC+3 browser is 15:50 ET
    rendered as 19:50, and the Activity Log confidently reports trades that
    never happened at that hour.

    That is not a display preference. Issue #3 was filed against the trading
    window with "bot traded at 19:30-19:50 ET" as the evidence, and those rows
    were 15:30-15:50 ET - the final half hour of the regular session. A shifted
    clock read as a broken risk control, so every response model below declares
    its datetimes as `UtcDatetime` and the instant is unambiguous in any
    client. Naive input is read as UTC, which is what it is; aware input is
    converted rather than relabelled.
    """
    if value is None or not isinstance(value, datetime):
        # Leave anything else to Pydantic: an ISO string from the broker, or a
        # value that should raise a validation error rather than be coerced.
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# A datetime that always carries an explicit UTC offset on the way out.
UtcDatetime = Annotated[datetime, BeforeValidator(_to_utc)]


# Auth
class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str


class VerifyResponse(BaseModel):
    valid: bool
    user: str


# Agent keys
class AgentKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # Defaults to read-only. An empty scope list would mint a key that
    # authenticates and then does nothing, which reads as a broken integration
    # rather than a deliberate restriction.
    scopes: List[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES), min_length=1)

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, v):
        unknown = [s for s in v if s not in ALL_SCOPES]
        if unknown:
            raise ValueError(f"Unknown scope(s): {', '.join(unknown)}")
        # Stable order so the stored value and the UI agree, and so two
        # identical requests produce identical rows.
        return [s for s in ALL_SCOPES if s in v]


class AgentKeyResponse(BaseModel):
    """Never contains the key itself - only the prefix, which is a lookup
    handle and cannot authenticate."""

    id: int
    label: str
    key_prefix: str
    scopes: List[str]
    created_at: UtcDatetime
    last_used_at: Optional[UtcDatetime] = None
    revoked_at: Optional[UtcDatetime] = None

    class Config:
        from_attributes = True


class AgentKeyCreated(AgentKeyResponse):
    """Returned exactly once, at creation. The plaintext is unrecoverable after
    this - only its sha256 was persisted."""

    key: str


class ScopeInfo(BaseModel):
    """Mirrors an entry in app.authz.SCOPE_INFO."""

    name: str
    label: str
    description: str
    danger: str
    granted_by_default: bool


class AgentRateLimits(BaseModel):
    """What an agent key is allowed to spend, so the numbers can be published
    rather than discovered.

    A 429 with no prior warning is a bad first experience for an integrator,
    and the alternative - a key that silently stops working - is worse. The
    admin who is about to hand out a key is the person who needs to know."""

    requests_per_window: int
    window_seconds: int
    read_limit: int
    write_limit: int


class ScopeCatalogue(BaseModel):
    scopes: List[ScopeInfo]
    rate_limits: AgentRateLimits


# Credentials
class CredentialsIn(BaseModel):
    key_id: str
    secret_key: str


class CredentialsStatus(BaseModel):
    has_keys: bool
    last_tested: Optional[UtcDatetime] = None


class TestConnectionResponse(BaseModel):
    status: str
    equity: Optional[str] = None
    buying_power: Optional[str] = None
    account_status: Optional[str] = None


# Strategy Profile Parameters
class SMACrossoverParams(BaseModel):
    fast_period: int = Field(default=10, ge=1)
    slow_period: int = Field(default=30, ge=2)
    position_size_pct: float = Field(default=0.10, gt=0, le=1)

    @model_validator(mode='after')
    def validate_fast_lt_slow(self):
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        return self


class RSIReversionParams(BaseModel):
    period: int = Field(default=14, ge=2)
    oversold: int = Field(default=30, ge=1, le=100)
    overbought: int = Field(default=70, ge=1, le=100)
    position_size_pct: float = Field(default=0.10, gt=0, le=1)

    @model_validator(mode='after')
    def validate_oversold_lt_overbought(self):
        if self.oversold >= self.overbought:
            raise ValueError("oversold must be less than overbought")
        return self


class MomentumBreakoutParams(BaseModel):
    lookback: int = Field(default=20, ge=2)
    position_size_pct: float = Field(default=0.10, gt=0, le=1)


# Strategy Profile
class StrategyProfileBase(BaseModel):
    name: str
    strategy_type: StrategyType
    parameters: dict
    # These four defaults are duplicated in `models.StrategyProfile`, and the
    # schema's win on create - `POST /profiles` does
    # `StrategyProfile(**data.model_dump())`, so every field arrives populated
    # and the column default is never reached. They used to be 0.10 x 5 here
    # and 0.15 x 10 on the column, which meant a profile created through the
    # API could never deploy more than 50% of the account no matter what the
    # model said (issue #2). If you change one, change both.
    risk_max_position_pct: float = Field(default=0.15, gt=0, le=1)
    risk_max_daily_loss_pct: float = Field(default=0.05, gt=0, le=1)
    risk_max_concurrent_positions: int = Field(default=10, ge=1)
    symbols: List[str] = Field(min_length=1)
    allow_fractional_shares: bool = False

    def check_position_caps(self) -> None:
        """Reject a profile whose strategy sizes above its own risk cap.

        Issues #2 and #6. `parameters.position_size_pct` is what the strategies
        order; `risk_max_position_pct` is what `RiskManager.check_position_size`
        allows. They are separately editable and were never related, so a user
        could set 25% and 10% and get a profile whose every order is refused
        with "Position size 25.00% exceeds max 10.00%" - the log fills with risk
        rejections, cash sits idle, and it reads as the risk system working
        rather than two fields contradicting each other.

        Compared on the *supplied* values, not the post-merge state, so that
        PATCH can run the same check on a profile that predates it. A profile
        with no `position_size_pct` is left alone: the strategy has a default
        for it, and demanding the field would break every client that relies on
        that default.

        Raises ValueError, which Pydantic turns into a 422 whose message
        names both numbers - the user has to be able to tell which field to
        change.
        """
        raw = (self.parameters or {}).get("position_size_pct")
        if raw is None:
            return
        cap = self.risk_max_position_pct
        if cap is None:
            return
        try:
            size, limit = float(raw), float(cap)
        except (TypeError, ValueError):
            return
        if size > limit:
            # Both forms of each number: the raw decimal is what the user has
            # to type back into the field, the percentage is how they think
            # about it. Naming only one of them means the reader has to
            # convert before they can act.
            raise ValueError(
                f"position_size_pct {size:.2f} ({size:.0%}) is greater than "
                f"risk_max_position_pct {limit:.2f} ({limit:.0%}). The strategy "
                f"orders at position_size_pct and the risk check refuses "
                f"anything above risk_max_position_pct, so every order this "
                f"profile produced would be rejected. Raise "
                f"risk_max_position_pct or lower position_size_pct."
            )


class StrategyProfileCreate(StrategyProfileBase):
    @model_validator(mode='after')
    def validate_position_caps(self):
        self.check_position_caps()
        return self


class StrategyProfileUpdate(BaseModel):
    name: Optional[str] = None
    strategy_type: Optional[StrategyType] = None
    parameters: Optional[dict] = None
    risk_max_position_pct: Optional[float] = Field(default=None, gt=0, le=1)
    risk_max_daily_loss_pct: Optional[float] = Field(default=None, gt=0, le=1)
    risk_max_concurrent_positions: Optional[int] = Field(default=None, ge=1)
    symbols: Optional[List[str]] = None
    enabled: Optional[bool] = None
    allow_fractional_shares: Optional[bool] = None

    def check_position_caps(self, existing: StrategyProfileBase | None) -> None:
        """The same check for PATCH, over the *merged* values.

        Merge-then-check, not check-the-request: a user who tightens
        `risk_max_position_pct` on a profile whose parameters already ask for
        more has created the contradiction just as surely as one who typed it
        into a create form, and validating only the request would let the edit
        through. It also has to be skipped when neither field is being touched,
        or a pre-existing bad row could never be renamed.
        """
        touched = ("parameters" in self.model_fields_set
                   or "risk_max_position_pct" in self.model_fields_set)
        if not touched or existing is None:
            return

        merged = StrategyProfileBase(
            name=existing.name,
            strategy_type=existing.strategy_type,
            parameters=self.parameters if "parameters" in self.model_fields_set else existing.parameters,
            risk_max_position_pct=(
                self.risk_max_position_pct
                if "risk_max_position_pct" in self.model_fields_set
                else existing.risk_max_position_pct
            ),
            risk_max_daily_loss_pct=existing.risk_max_daily_loss_pct,
            risk_max_concurrent_positions=existing.risk_max_concurrent_positions,
            symbols=existing.symbols,
        )
        merged.check_position_caps()


class StrategyProfileResponse(StrategyProfileBase):
    id: int
    enabled: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
    allow_fractional_shares: bool = False
    # How much of the account this profile can actually put to work, as a
    # fraction of equity. Not a column: it is a property on the ORM model
    # (`StrategyProfile.max_deployable_pct`), read here through
    # from_attributes, so the number the API reports and the number the worker
    # logs are the same arithmetic rather than two copies of it.
    #
    # This is the answer to "why is my cash sitting idle". The live RSI
    # profile reports 0.75: five concurrent positions at 15% each, so a quarter
    # of the account cannot be reached at all. Waiting for more signals does not
    # help; only raising a cap does.
    max_deployable_pct: float = 0.0

    class Config:
        from_attributes = True


# Bot Config
class BotConfigResponse(BaseModel):
    schedule_cron: str
    market_hours_only: bool
    active_profile_id: Optional[int] = None
    is_running: bool
    updated_at: UtcDatetime
    # The IANA zone the worker evaluates the cron expression in, i.e.
    # settings.bot_timezone, i.e. exactly what it hands to CronTrigger. Not a
    # column: it is a server setting, so the UI has to be told rather than
    # hardcode "ET" - the Schedule page claiming UTC is what made a "9-16"
    # schedule fire 05:00-12:00 Eastern in the first place.
    cron_timezone: str
    # The share of equity the strategies are allowed to size positions
    # against. Issue #6: the "money to invest" control. 1.0 means the full
    # account is investable; 0.8 holds a 20% cash reserve. Distinct from the
    # per-profile caps, which bound a single position and the size of the
    # book - this bounds the total.
    capital_allocation_pct: float

    class Config:
        from_attributes = True


class AuditLogResponse(BaseModel):
    id: int
    created_at: UtcDatetime
    actor_kind: str
    actor_label: Optional[str] = None
    key_id: Optional[int] = None
    method: str
    path: str
    action: str
    status_code: int
    detail: Optional[str] = None
    client_ip: Optional[str] = None

    class Config:
        from_attributes = True


class BotConfigUpdate(BaseModel):
    schedule_cron: Optional[str] = None
    market_hours_only: Optional[bool] = None
    active_profile_id: Optional[int] = None
    # "Money to invest". Applied to every strategy's position sizing, so a
    # value of 0.8 sizes against 80% of equity and leaves 20% in cash.
    # `gt=0` rather than `ge=0`: 0% would mean "never invest", which is the
    # stop button, not an allocation.
    capital_allocation_pct: Optional[float] = Field(default=None, gt=0, le=1)


# Dashboard
class AccountResponse(BaseModel):
    equity: float
    portfolio_value: float
    cash: float
    buying_power: float
    day_pl: float
    total_pl: float
    status: str


class PositionResponse(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    cost_basis: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str


class OrderResponse(BaseModel):
    """Mirrors a live Alpaca order.

    `side`, `order_type` and `status` are plain `str`, deliberately not the
    local OrderSide/OrderType/OrderStatus enums. Alpaca's vocabulary is far
    larger than ours (18 order statuses, plus stop/stop_limit/trailing_stop
    order types), and this endpoint reports whatever the broker holds. Typing
    these as our enums made Pydantic reject a routine `accepted` order and
    turned GET /dashboard/orders into a 500 the moment the bot traded.
    """

    id: str
    symbol: str
    qty: float
    side: str
    order_type: str
    limit_price: Optional[float] = None
    status: str
    filled_price: Optional[float] = None
    filled_qty: Optional[float] = None
    submitted_at: UtcDatetime


class EquityPoint(BaseModel):
    timestamp: UtcDatetime
    equity: float


class TradeLogResponse(BaseModel):
    """One row of the bot's own activity log.

    `status` is a plain `str` because TradeLog.status is a free-text column.
    The worker writes values from the local OrderStatus enum via
    local_order_status(), but rows written before that mapping existed hold raw
    Alpaca values ('accepted', 'new', ...). Typing this as the enum made the
    Activity tab 500 on exactly those rows. See app/models.py TradeLog.status.
    """

    id: int
    timestamp: UtcDatetime
    profile_id: Optional[int] = None
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None
    status: str
    alpaca_order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_qty: Optional[float] = None
    message: str
    error_details: Optional[str] = None

    class Config:
        from_attributes = True


class MarketClockResponse(BaseModel):
    timestamp: UtcDatetime
    is_open: bool
    next_open: Optional[UtcDatetime] = None
    next_close: Optional[UtcDatetime] = None
    # `is_open` is true across pre-market, regular hours and after-hours, so it
    # cannot tell a user whether the bot is allowed to trade right now. This is
    # the same subdivision the worker gates on (app/bot/market_hours.py): one
    # of SESSION_STATES. Reporting it removes the temptation for the frontend
    # to re-derive the session from a local clock, which is how a 15:50 ET
    # trade came to be read as 19:50.
    session_state: str
    # The zone market times are quoted in, served rather than hardcoded for the
    # same reason `cron_timezone` is (gotcha 16).
    timezone: str
    # Whether the worker's market_hours_only flag currently permits trading in
    # this session, so the dashboard can explain a bot that is idle rather
    # than looking broken.
    trading_allowed: bool
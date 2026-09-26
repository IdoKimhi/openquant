from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import datetime
from app.models import StrategyType, OrderSide, OrderType, OrderStatus


# Auth
class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str


class VerifyResponse(BaseModel):
    valid: bool
    user: str


# Credentials
class CredentialsIn(BaseModel):
    key_id: str
    secret_key: str


class CredentialsStatus(BaseModel):
    has_keys: bool
    last_tested: Optional[datetime] = None


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
    risk_max_position_pct: float = Field(default=0.10, gt=0, le=1)
    risk_max_daily_loss_pct: float = Field(default=0.05, gt=0, le=1)
    risk_max_concurrent_positions: int = Field(default=5, ge=1)
    symbols: List[str] = Field(min_length=1)


class StrategyProfileCreate(StrategyProfileBase):
    pass


class StrategyProfileUpdate(BaseModel):
    name: Optional[str] = None
    strategy_type: Optional[StrategyType] = None
    parameters: Optional[dict] = None
    risk_max_position_pct: Optional[float] = Field(default=None, gt=0, le=1)
    risk_max_daily_loss_pct: Optional[float] = Field(default=None, gt=0, le=1)
    risk_max_concurrent_positions: Optional[int] = Field(default=None, ge=1)
    symbols: Optional[List[str]] = None
    enabled: Optional[bool] = None


class StrategyProfileResponse(StrategyProfileBase):
    id: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Bot Config
class BotConfigResponse(BaseModel):
    schedule_cron: str
    market_hours_only: bool
    active_profile_id: Optional[int] = None
    is_running: bool
    updated_at: datetime

    class Config:
        from_attributes = True


class BotConfigUpdate(BaseModel):
    schedule_cron: Optional[str] = None
    market_hours_only: Optional[bool] = None
    active_profile_id: Optional[int] = None


# Dashboard
class AccountResponse(BaseModel):
    equity: float
    cash: float
    buying_power: float
    day_pl: float
    total_pl: float
    status: str


class PositionResponse(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    side: str


class OrderResponse(BaseModel):
    id: str
    symbol: str
    qty: float
    side: OrderSide
    order_type: OrderType
    limit_price: Optional[float] = None
    status: OrderStatus
    filled_price: Optional[float] = None
    filled_qty: Optional[float] = None
    submitted_at: datetime


class EquityPoint(BaseModel):
    timestamp: datetime
    equity: float


class TradeLogResponse(BaseModel):
    id: int
    timestamp: datetime
    profile_id: Optional[int] = None
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None
    status: OrderStatus
    alpaca_order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_qty: Optional[float] = None
    message: str
    error_details: Optional[str] = None

    class Config:
        from_attributes = True


class MarketClockResponse(BaseModel):
    is_open: bool
    next_open: Optional[datetime] = None
    next_close: Optional[datetime] = None
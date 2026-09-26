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
    risk_max_position_pct = Column(Float, default=0.10)
    risk_max_daily_loss_pct = Column(Float, default=0.05)
    risk_max_concurrent_positions = Column(Integer, default=5)
    symbols = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotConfig(Base):
    __tablename__ = "bot_config"

    id = Column(Integer, primary_key=True, index=True)
    schedule_cron = Column(String, default="*/5 9-16 * * MON-FRI")
    market_hours_only = Column(Boolean, default=True)
    active_profile_id = Column(Integer, ForeignKey("strategy_profiles.id"), nullable=True)
    is_running = Column(Boolean, default=False)
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
    status = Column(SQLEnum(OrderStatus), default=OrderStatus.submitted)
    alpaca_order_id = Column(String, nullable=True)
    filled_price = Column(Float, nullable=True)
    filled_qty = Column(Float, nullable=True)
    message = Column(Text, nullable=False)
    error_details = Column(Text, nullable=True)

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
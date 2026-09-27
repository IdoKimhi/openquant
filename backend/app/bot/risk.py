# Risk Management Module
# Handles position sizing, daily loss limits, max concurrent positions, and kill-switch

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models import StrategyProfile, TradeLog


@dataclass
class RiskCheckResult:
    """Result of a risk check"""
    allowed: bool
    reason: Optional[str] = None


def compute_daily_loss_pct(equity: float, last_equity: Optional[float]) -> float:
    """Today's loss as a fraction of the previous close's equity.

    Alpaca reports the account's equity and the equity at the previous close,
    so the broker's own numbers are the source of truth for the daily loss
    limit. A gain is never a loss, hence the clamp at zero. A missing or
    non-positive baseline yields 0.0 so an unknown value can never fabricate a
    breach.
    """
    if not last_equity or last_equity <= 0:
        return 0.0
    return max(0.0, (last_equity - equity) / last_equity)


class RiskManager:
    """Manages risk controls for trading bot"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def calculate_position_size(
        self,
        profile: StrategyProfile,
        equity: float,
        current_price: float
    ) -> int:
        """
        Calculate position size based on equity and position_size_pct parameter.
        Returns at least 1 share if equity allows any position.
        """
        position_size_pct = profile.parameters.get("position_size_pct", 0.10)
        max_position_value = equity * position_size_pct
        qty = int(max_position_value / current_price)
        return max(1, qty)
    
    def check_position_size(
        self,
        profile: StrategyProfile,
        equity: float,
        qty: int,
        current_price: float
    ) -> RiskCheckResult:
        """Check if proposed position size is within max_position_pct limit"""
        max_position_pct = profile.risk_max_position_pct
        position_value = qty * current_price
        position_pct = position_value / equity if equity > 0 else 1.0
        
        if position_pct > max_position_pct:
            return RiskCheckResult(
                allowed=False,
                reason=f"Position size {position_pct:.2%} exceeds max {max_position_pct:.2%}"
            )
        return RiskCheckResult(allowed=True)
    
    def check_daily_loss(
        self,
        profile: StrategyProfile,
        equity: float,
        daily_loss_pct: Optional[float] = None
    ) -> RiskCheckResult:
        """Check if today's losses exceed max_daily_loss_pct

        Prefers `daily_loss_pct` (derived from the broker's equity vs
        last_equity) because TradeLog.pnl is never populated - nothing in the
        app writes it, so summing it always yields zero and the limit could
        never trigger. Falls back to the local sum when no broker figure is
        supplied.
        """
        if daily_loss_pct is None:
            today = date.today()
            today_start = datetime.combine(today, datetime.min.time())
            today_end = datetime.combine(today, datetime.max.time())

            # Query today's completed trades with PnL
            trades = self.db.query(TradeLog).filter(
                TradeLog.profile_id == profile.id,
                TradeLog.timestamp >= today_start,
                TradeLog.timestamp <= today_end,
                TradeLog.pnl.isnot(None)
            ).all()

            total_loss = sum(trade.pnl for trade in trades if trade.pnl < 0)
            loss_pct = abs(total_loss) / equity if equity > 0 else 1.0
        else:
            loss_pct = max(0.0, daily_loss_pct)

        if loss_pct > profile.risk_max_daily_loss_pct:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss {loss_pct:.2%} exceeds max {profile.risk_max_daily_loss_pct:.2%}"
            )
        return RiskCheckResult(allowed=True)
    
    def check_max_concurrent_positions(
        self,
        profile: StrategyProfile,
        current_positions_count: int
    ) -> RiskCheckResult:
        """Block opening a position that would exceed the concurrency limit.

        Only reached when about to open one, so the count compared against the
        limit is the number already held: a full book must refuse the next
        entry rather than exceed the cap.
        """
        if current_positions_count >= profile.risk_max_concurrent_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"Concurrent positions {current_positions_count} reaches or exceeds max {profile.risk_max_concurrent_positions}"
            )
        return RiskCheckResult(allowed=True)
    
    def should_trigger_kill_switch(
        self,
        profile: StrategyProfile,
        equity: float,
        daily_loss_pct: Optional[float] = None
    ) -> RiskCheckResult:
        """
        Check if kill-switch should trigger (daily loss exceeds threshold).
        This is essentially the same as check_daily_loss but with more aggressive action.
        """
        return self.check_daily_loss(profile, equity, daily_loss_pct)
    
    def validate_order(
        self,
        profile: StrategyProfile,
        equity: float,
        qty: int,
        current_price: float,
        current_positions_count: int,
        side: str,
        daily_loss_pct: Optional[float] = None
    ) -> RiskCheckResult:
        """
        Validate an order against all risk checks.
        For sell orders (flattening), only check position size is valid.
        For buy orders, check all risk limits.
        """
        # Always check position size
        result = self.check_position_size(profile, equity, qty, current_price)
        if not result.allowed:
            return result
        
        # For buy orders, check all risk limits
        if side.lower() == "buy":
            # Check daily loss
            result = self.check_daily_loss(profile, equity, daily_loss_pct)
            if not result.allowed:
                return result
            
            # Check concurrent positions
            result = self.check_max_concurrent_positions(profile, current_positions_count)
            if not result.allowed:
                return result
            
            # Check kill switch
            result = self.should_trigger_kill_switch(profile, equity, daily_loss_pct)
            if not result.allowed:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Kill switch triggered: {result.reason}"
                )
        
        return RiskCheckResult(allowed=True)
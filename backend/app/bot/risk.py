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
        equity: float
    ) -> RiskCheckResult:
        """Check if today's realized losses exceed max_daily_loss_pct"""
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
        daily_loss_pct = abs(total_loss) / equity if equity > 0 else 1.0
        
        if daily_loss_pct > profile.risk_max_daily_loss_pct:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss {daily_loss_pct:.2%} exceeds max {profile.risk_max_daily_loss_pct:.2%}"
            )
        return RiskCheckResult(allowed=True)
    
    def check_max_concurrent_positions(
        self,
        profile: StrategyProfile,
        current_positions_count: int
    ) -> RiskCheckResult:
        """Check if current open positions count is within limit"""
        if current_positions_count > profile.risk_max_concurrent_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"Concurrent positions {current_positions_count} exceeds max {profile.risk_max_concurrent_positions}"
            )
        return RiskCheckResult(allowed=True)
    
    def should_trigger_kill_switch(
        self,
        profile: StrategyProfile,
        equity: float
    ) -> RiskCheckResult:
        """
        Check if kill-switch should trigger (daily loss exceeds threshold).
        This is essentially the same as check_daily_loss but with more aggressive action.
        """
        return self.check_daily_loss(profile, equity)
    
    def validate_order(
        self,
        profile: StrategyProfile,
        equity: float,
        qty: int,
        current_price: float,
        current_positions_count: int,
        side: str
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
            result = self.check_daily_loss(profile, equity)
            if not result.allowed:
                return result
            
            # Check concurrent positions
            result = self.check_max_concurrent_positions(profile, current_positions_count)
            if not result.allowed:
                return result
            
            # Check kill switch
            result = self.should_trigger_kill_switch(profile, equity)
            if not result.allowed:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Kill switch triggered: {result.reason}"
                )
        
        return RiskCheckResult(allowed=True)
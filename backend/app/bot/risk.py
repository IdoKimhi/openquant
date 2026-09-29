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


def allocation_pct(value) -> float:
    """The capital allocation in force, clamped, with NULL meaning "all of it".

    This database is a file on a volume, upgraded by `ensure_schema` in
    app/db.py, so a row written before `capital_allocation_pct` existed has
    NULL there. That is not a hypothetical: it is what every row in a
    pre-upgrade deployment holds, and `equity * None` raises TypeError inside
    the trading cycle - a bot that stops trading with a traceback in the log.

    Clamped rather than trusted, because this value decides how much real
    capital gets committed.
    """
    if value is None:
        return 1.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def size_qty(value: float, price: float, allow_fractional: bool = False) -> float:
    """How many shares `value` buys at `price`.

    The one place position sizing happens for every strategy. All three used to
    inline `int((equity * pct) / price)`, which truncates toward zero and so
    throws away up to a full share per position - on a $700 stock that is $700
    per signal, and it is a large part of the "only 70% deployed" complaint
    (issue #2).

    Whole shares by default, matching what the broker accepts for every order
    type. Alpaca supports fractional quantities for market orders only, so
    fractional sizing is opt-in and the caller has to know that; see
    AlpacaClient.submit_order, which refuses a fractional limit order rather
    than letting the broker reject it.

    Returns 0.0 when the amount cannot buy the smallest permitted increment,
    which callers already treat as "no signal" - rounding *up* to 1 share would
    breach a 15% cap on a small account.
    """
    if price is None or price <= 0:
        return 0.0
    if not allow_fractional:
        return float(int(value / price))
    # Two decimals, which is what Alpaca accepts for a fractional stock
    # quantity. Truncated, never rounded up: the cap is a ceiling.
    return float(int(value / price * 100) / 100)


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

    @staticmethod
    def investable_equity(equity: float, capital_allocation_pct) -> float:
        """The equity position sizing and every risk limit are measured against.

        Issue #6, the "money to invest" control. A 0.8 allocation means 80% of
        the account may be committed and 20% is held back.

        One base for everything, deliberately. Applying the allocation only to
        the strategies would shrink the positions while leaving the limits
        measured against the whole account, so a 15%-of-equity position cap
        would become 18.75% of what the operator asked to be investable - the
        cash reserve would exist and the risk envelope would not respect it.
        """
        return max(0.0, float(equity) * allocation_pct(capital_allocation_pct))

    def calculate_position_size(
        self,
        profile: StrategyProfile,
        equity: float,
        current_price: float
    ) -> float:
        """
        Calculate position size from investable equity and position_size_pct.

        Delegates to the shared sizing helper so this and the strategies cannot
        disagree about what a position is worth.
        """
        position_size_pct = (profile.parameters or {}).get("position_size_pct", 0.10)
        return size_qty(
            equity * float(position_size_pct),
            current_price,
            allow_fractional=bool(profile.allow_fractional_shares),
        )
    
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

        Every check here gates *new* exposure, so they apply to buys only.
        This used to be subtler than it needed to be: the position-size cap
        ran on sells too, with a comment claiming that was intended. It was
        the one check that could trap a position, because a position bought
        at the cap that has since doubled is over the cap *by construction* -
        and that is exactly what RSI reversion sells into. The cap would
        refuse the exit, so the strategy could not take profits on precisely
        the positions it most wanted to close; only the kill-switch could
        flatten them. Key decision #7: "risk limits are gates, not
        flatteners ... deliberately always permits sells."

        `equity` here is the *investable* base: the denominator the caps are
        measured against is `investable_equity`, not account equity, so a
        0.8 allocation keeps the limits on the 80% the operator asked to be
        investable (see RiskManager.investable_equity).
        """
        if side.lower() != "buy":
            # Sells are exempt from every gate above. They were exempt from
            # the daily-loss and kill-switch checks all along; the position
            # cap is the one that had to be moved into the buy branch.
            return RiskCheckResult(allowed=True)

        # Check position size
        result = self.check_position_size(profile, equity, qty, current_price)
        if not result.allowed:
            return result

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
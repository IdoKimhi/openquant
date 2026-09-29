# Tests for Risk Management Module

import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
from app.bot.risk import RiskManager, RiskCheckResult, compute_daily_loss_pct
from app.models import StrategyProfile, BotConfig, TradeLog


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def risk_manager(mock_db):
    return RiskManager(mock_db)


@pytest.fixture
def active_profile():
    return StrategyProfile(
        id=1,
        name="Test Profile",
        strategy_type="sma_crossover",
        parameters={"fast_period": 10, "slow_period": 30, "position_size_pct": 0.10},
        risk_max_position_pct=0.10,
        risk_max_daily_loss_pct=0.05,
        risk_max_concurrent_positions=5,
        symbols=["AAPL", "GOOGL"],
        enabled=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


@pytest.fixture
def bot_config():
    return BotConfig(
        id=1,
        schedule_cron="*/5 9-16 * * MON-FRI",
        market_hours_only=True,
        active_profile_id=1,
        is_running=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


class TestRiskManager:
    def test_check_position_size_within_limit(self, risk_manager, active_profile):
        """Test position size check passes when within limits"""
        equity = 100000.0
        qty = 50  # 50 * 150 = 7500, which is 7.5% of equity (within 10% limit)
        current_price = 150.0
        
        result = risk_manager.check_position_size(
            profile=active_profile,
            equity=equity,
            qty=qty,
            current_price=current_price
        )
        
        assert result.allowed is True
        assert result.reason is None

    def test_check_position_size_exceeds_limit(self, risk_manager, active_profile):
        """Test position size check fails when exceeding max position pct"""
        equity = 100000.0
        qty = 200  # 200 * 150 = 30000, which is 30% of equity (exceeds 10% limit)
        current_price = 150.0
        
        result = risk_manager.check_position_size(
            profile=active_profile,
            equity=equity,
            qty=qty,
            current_price=current_price
        )
        
        assert result.allowed is False
        assert "exceeds max" in result.reason.lower()

    def test_check_daily_loss_within_limit(self, risk_manager, active_profile, mock_db):
        """Test daily loss check passes when within limits"""
        # Mock trades for today with small loss
        today = date.today()
        mock_trades = [
            MagicMock(side="sell", qty=10, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-100.0),
            MagicMock(side="sell", qty=5, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-50.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        equity = 100000.0
        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=equity
        )
        
        # Total loss = 150, which is 0.15% of equity (within 5% limit)
        assert result.allowed is True
        assert result.reason is None

    def test_check_daily_loss_exceeds_limit(self, risk_manager, active_profile, mock_db):
        """Test daily loss check fails when exceeding max daily loss pct"""
        today = date.today()
        # Large loss exceeding 5% of equity
        mock_trades = [
            MagicMock(side="sell", qty=100, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-6000.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        equity = 100000.0
        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=equity
        )
        
        # Loss = 6000, which is 6% of equity (exceeds 5% limit)
        assert result.allowed is False
        assert "exceeds max" in result.reason.lower()

    def test_check_daily_loss_ignores_previous_days(self, risk_manager, active_profile, mock_db):
        """Test daily loss check only considers today's trades"""
        yesterday = date.fromordinal(date.today().toordinal() - 1)
        # The mock should only return today's trades (the SQL filter handles date filtering)
        mock_trades = [
            MagicMock(side="sell", qty=10, price=150.0, timestamp=datetime.combine(date.today(), datetime.min.time()), pnl=-100.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        equity = 100000.0
        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=equity
        )
        
        # Only today's loss of 100 (0.1%) counts, well within 5% limit
        assert result.allowed is True

    def test_check_max_concurrent_positions_within_limit(self, risk_manager, active_profile, mock_db):
        """Test concurrent positions check passes when within limit"""
        # Mock 3 open positions (within limit of 5)
        mock_positions = [
            MagicMock(symbol="AAPL", qty=10),
            MagicMock(symbol="GOOGL", qty=5),
            MagicMock(symbol="MSFT", qty=20),
        ]
        # This would come from Alpaca, but risk manager checks against profile limit
        result = risk_manager.check_max_concurrent_positions(
            profile=active_profile,
            current_positions_count=3
        )
        
        assert result.allowed is True
        assert result.reason is None

    def test_check_max_concurrent_positions_exceeds_limit(self, risk_manager, active_profile):
        """Test concurrent positions check fails when opening would exceed limit

        This guard is only reached when about to OPEN a new position, so the
        count that matters is the post-trade count. With a limit of 5, having
        5 already open means the 6th would exceed it.
        """
        result = risk_manager.check_max_concurrent_positions(
            profile=active_profile,
            current_positions_count=4  # One slot left
        )
        assert result.allowed is True

        result = risk_manager.check_max_concurrent_positions(
            profile=active_profile,
            current_positions_count=5  # Full, a 6th would exceed the limit
        )
        assert result.allowed is False
        assert "exceeds max" in result.reason.lower()

    def test_calculate_position_size(self, risk_manager, active_profile):
        """Test position size calculation based on equity and risk params"""
        equity = 100000.0
        current_price = 150.0
        
        qty = risk_manager.calculate_position_size(
            profile=active_profile,
            equity=equity,
            current_price=current_price
        )
        
        # 10% of 100000 = 10000, / 150 = 66 shares
        assert qty == 66

    def test_calculate_position_size_will_not_overshoot_the_cap(self, risk_manager, active_profile):
        """A budget too small for a whole share returns 0, not 1.

        This used to be `return max(1, qty)`, which guarantees a trade by
        buying a share the budget cannot pay for: 10% of $1000 is $100, a
        $150 stock is one share, and the resulting order is 15% of equity
        against a 10% cap. The cap is a limit, not a target, so rounding *up*
        to it is a silent risk-limit breach - worse than not trading, because
        nothing reports it.

        The real answer to a too-small budget is fractional sizing
        (`allow_fractional_shares`), which is asserted below; rounding up was
        only ever a way of pretending the problem did not exist.
        """
        equity = 1000.0  # 10% = $100
        current_price = 150.0  # one share is $150

        qty = risk_manager.calculate_position_size(
            profile=active_profile,
            equity=equity,
            current_price=current_price
        )

        assert qty == 0

    def test_fractional_sizing_uses_a_budget_a_whole_share_would_not(self, risk_manager, active_profile):
        """With fractional shares on, the same budget does deploy."""
        equity = 1000.0
        current_price = 150.0
        active_profile.allow_fractional_shares = True

        qty = risk_manager.calculate_position_size(
            profile=active_profile,
            equity=equity,
            current_price=current_price
        )

        # $100 / $150 = 0.6666 -> 0.66 shares, i.e. $99 of the $100 budget.
        assert qty == pytest.approx(0.66, abs=1e-9)

    def test_should_trigger_kill_switch(self, risk_manager, active_profile, mock_db):
        """Test kill switch triggers when daily loss exceeds threshold"""
        today = date.today()
        mock_trades = [
            MagicMock(side="sell", qty=100, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-6000.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        equity = 100000.0
        result = risk_manager.should_trigger_kill_switch(
            profile=active_profile,
            equity=equity
        )
        
        assert result.allowed is False  # Kill switch should trigger
        assert "kill switch" in result.reason.lower() or "daily loss" in result.reason.lower()

    def test_should_not_trigger_kill_switch_when_within_limits(self, risk_manager, active_profile, mock_db):
        """Test kill switch doesn't trigger when within limits"""
        today = date.today()
        mock_trades = [
            MagicMock(side="sell", qty=10, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-100.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        equity = 100000.0
        result = risk_manager.should_trigger_kill_switch(
            profile=active_profile,
            equity=equity
        )
        
        assert result.allowed is True  # Kill switch should NOT trigger

    def test_validate_order_passes_all_checks(self, risk_manager, active_profile, mock_db):
        """Test order validation passes when all risk checks pass"""
        # Mock all checks to pass
        today = date.today()
        mock_trades = [
            MagicMock(side="sell", qty=10, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-100.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=50,
            current_price=150.0,
            current_positions_count=2,
            side="buy"
        )
        
        assert result.allowed is True

    def test_validate_order_fails_on_any_check(self, risk_manager, active_profile, mock_db):
        """Test order validation fails if any single check fails"""
        today = date.today()
        # Large daily loss
        mock_trades = [
            MagicMock(side="sell", qty=100, price=150.0, timestamp=datetime.combine(today, datetime.min.time()), pnl=-6000.0),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_trades
        
        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=50,
            current_price=150.0,
            current_positions_count=2,
            side="buy"
        )
        
        assert result.allowed is False
        assert "daily loss" in result.reason.lower() or "kill switch" in result.reason.lower()


class TestExitsAreNeverGatedByThePositionCap:
    """A position-size cap is a limit on *new* exposure, not on getting out.

    Key decision #7: "Risk limits are gates, not flatteners ... deliberately
    always permits sells, so a breach can never trap a position." Only
    `POST /bot/kill-switch` closes positions.

    That invariant was half-true. The daily-loss and kill-switch checks were
    buy-only, but `validate_order` ran `check_position_size` on *every* order
    including sells - so a position that grew past `risk_max_position_pct`
    could not be sold by the strategy at all.

    This is the ordinary way that happens, not an edge case: a position is
    bought at 10% of equity because that is the cap, the position works, the
    price doubles, and the position is now 20% of equity. RSI reversion sells
    when it is overbought - precisely the case where a winner has run up - so
    the strategy could not take profits on exactly the positions it most
    wanted to close.

    The existing `test_validate_order_still_allows_exits_when_limit_breached`
    passed against this because it sold a position *within* the cap, so it
    never exercised the trap its own name claims to guard. Asserting that sells
    pass while handing `validate_order` a sell that respects every cap only
    proves the caps are not being applied to sells at all - which is also not
    the thing being claimed.
    """

    def test_a_position_that_ran_past_the_cap_can_still_be_sold(
        self, risk_manager, active_profile, mock_db
    ):
        mock_db.query.return_value.filter.return_value.all.return_value = []

        # Bought at the 10% cap; the price doubled, so the position is now
        # 20000/100000 = 20% of equity - twice what the profile allows.
        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=100,
            current_price=200.0,
            current_positions_count=2,
            side="sell",
            daily_loss_pct=0.0,
        )

        assert result.allowed is True, (
            f"a position past the cap must still be sellable, got: {result.reason}"
        )

    def test_the_same_order_as_a_buy_is_still_refused(
        self, risk_manager, active_profile, mock_db
    ):
        """The exemption is for exits only.

        Without this, "sells bypass the cap" could be satisfied by deleting
        the check entirely - which is the other way to be wrong, and a much
        worse one: nothing would bound a new position any more.
        """
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=100,
            current_price=200.0,
            current_positions_count=2,
            side="buy",
            daily_loss_pct=0.0,
        )

        assert result.allowed is False
        assert "exceeds max" in result.reason

    def test_a_sell_inside_the_cap_still_passes(
        self, risk_manager, active_profile, mock_db
    ):
        """Regression guard for the case that already worked.

        Without this, "sells bypass the cap" would also be satisfied by
        deleting the check outright - which is the other way to be wrong, and a
        much worse one, because nothing would bound a new position any more.
        """
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=1,
            current_price=200.0,
            current_positions_count=2,
            side="sell",
            daily_loss_pct=0.0,
        )

        assert result.allowed is True


class TestTheCapIsMeasuredAgainstTheInvestableBase:
    """`investable_equity` is the base for sizing *and* for the limits.

    The docstring on `RiskManager.investable_equity` claims one base serves
    both, and gives the reason: applying the allocation to sizing alone would
    leave the limits on the whole account, "so a 15% cap would silently
    become 18.75% of the money the operator asked to be investable".

    That was not true of the call site. The worker passed raw account equity
    into `validate_order`, so the cap was measured against a base 25% larger
    than intended at a 0.8 allocation. Documenting an invariant the code does
    not hold is worse than not documenting it, because the next reader trusts
    the comment - and would then be surprised when a 10% cap permitted 12.5%.
    """

    def test_the_base_decides_what_the_cap_permits(
        self, risk_manager, active_profile, mock_db
    ):
        mock_db.query.return_value.filter.return_value.all.return_value = []

        # The cap is 10%. 9000 is 9% of a 100000 account - inside it - but
        # 11.25% of the 80000 investable at a 0.8 allocation - outside it.
        # Measured against the wrong base, the cap is 25% looser than the
        # operator configured, which is precisely what the reserve is for.
        def attempt(equity):
            return risk_manager.validate_order(
                profile=active_profile,
                equity=equity,
                qty=45,
                current_price=200.0,
                current_positions_count=2,
                side="buy",
                daily_loss_pct=0.0,
            )

        on_raw = attempt(100000.0)
        on_investable = attempt(80000.0)

        assert on_raw.allowed is True
        assert on_investable.allowed is False
        assert "exceeds max" in on_investable.reason

    def test_an_order_on_the_cap_is_allowed_on_the_investable_base(
        self, risk_manager, active_profile, mock_db
    ):
        """The cap is a ceiling, so an order exactly on it is permitted.

        `check_position_size` uses `>`, not `>=`. Getting that backwards would
        make a position exactly at the configured size unsizable - and the
        strategy sizes *at* `position_size_pct`, so the common case would be
        the broken one.
        """
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.validate_order(
            profile=active_profile,
            equity=80000.0,          # investable at a 0.8 allocation
            qty=40,                   # 40 x 200 = 8000 = exactly 10%
            current_price=200.0,
            current_positions_count=2,
            side="buy",
            daily_loss_pct=0.0,
        )

        assert result.allowed is True


class TestRiskCheckResult:
    def test_risk_check_result_allowed(self):
        result = RiskCheckResult(allowed=True, reason=None)
        assert result.allowed is True
        assert result.reason is None

    def test_risk_check_result_denied(self):
        result = RiskCheckResult(allowed=False, reason="Test reason")
        assert result.allowed is False
        assert result.reason == "Test reason"


class TestComputeDailyLossPct:
    """The live daily P&L comes from the broker, not from local trade rows."""

    def test_flat_account_has_no_loss(self):
        assert compute_daily_loss_pct(equity=10000.0, last_equity=10000.0) == 0.0

    def test_loss_is_measured_against_previous_close(self):
        # Down 500 on a 10000 base = 5%
        assert compute_daily_loss_pct(equity=9500.0, last_equity=10000.0) == pytest.approx(0.05)

    def test_gain_is_never_a_loss(self):
        assert compute_daily_loss_pct(equity=11000.0, last_equity=10000.0) == 0.0

    def test_missing_last_equity_is_not_a_loss(self):
        assert compute_daily_loss_pct(equity=10000.0, last_equity=None) == 0.0
        assert compute_daily_loss_pct(equity=10000.0, last_equity=0) == 0.0


class TestDailyLossUsesBrokerReportedPnl:
    """Regression tests for a kill-switch that could never fire.

    Nothing in the app ever wrote TradeLog.pnl, so the local sum was always
    empty and check_daily_loss always returned allowed - the daily loss limit
    and the kill-switch gate were permanently inert. The broker's own
    equity vs last_equity is now the source of truth.
    """

    def test_broker_loss_blocks_despite_empty_trade_log(self, risk_manager, active_profile, mock_db):
        # Exactly production state: no pnl rows anywhere in the database
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=100000.0,
            daily_loss_pct=0.06
        )

        assert result.allowed is False
        assert "exceeds max" in result.reason.lower()

    def test_no_broker_loss_allows(self, risk_manager, active_profile, mock_db):
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=100000.0,
            daily_loss_pct=0.0
        )

        assert result.allowed is True

    def test_falls_back_to_trade_log_when_broker_value_absent(self, risk_manager, active_profile, mock_db):
        # daily_loss_pct omitted -> legacy local calculation still applies
        today = date.today()
        mock_db.query.return_value.filter.return_value.all.return_value = [
            MagicMock(pnl=-6000.0, timestamp=datetime.combine(today, datetime.min.time())),
        ]

        result = risk_manager.check_daily_loss(
            profile=active_profile,
            equity=100000.0
        )

        assert result.allowed is False

    def test_kill_switch_triggers_on_broker_loss(self, risk_manager, active_profile, mock_db):
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.should_trigger_kill_switch(
            profile=active_profile,
            equity=100000.0,
            daily_loss_pct=0.06
        )

        assert result.allowed is False

    def test_validate_order_blocks_buy_on_broker_loss(self, risk_manager, active_profile, mock_db):
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=50,
            current_price=150.0,
            current_positions_count=2,
            side="buy",
            daily_loss_pct=0.06
        )

        assert result.allowed is False

    def test_validate_order_still_allows_exits_when_limit_breached(self, risk_manager, active_profile, mock_db):
        """A breached loss limit must never trap a position: sells always pass."""
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = risk_manager.validate_order(
            profile=active_profile,
            equity=100000.0,
            qty=50,
            current_price=150.0,
            current_positions_count=2,
            side="sell",
            daily_loss_pct=0.50
        )

        assert result.allowed is True
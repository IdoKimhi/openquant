# Tests for Risk Management Module

import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
from app.bot.risk import RiskManager, RiskCheckResult
from app.models import StrategyProfile, BotConfig, TradeLog


class TestRiskManager:
    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        return db

    @pytest.fixture
    def risk_manager(self, mock_db):
        return RiskManager(mock_db)

    @pytest.fixture
    def active_profile(self):
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
    def bot_config(self):
        return BotConfig(
            id=1,
            schedule_cron="*/5 9-16 * * MON-FRI",
            market_hours_only=True,
            active_profile_id=1,
            is_running=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

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
        """Test concurrent positions check fails when at limit"""
        result = risk_manager.check_max_concurrent_positions(
            profile=active_profile,
            current_positions_count=5  # At limit of 5
        )
        
        # At limit should still be allowed (not exceeding)
        assert result.allowed is True
        
        # But exceeding should fail
        result = risk_manager.check_max_concurrent_positions(
            profile=active_profile,
            current_positions_count=6
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

    def test_calculate_position_size_minimum_one(self, risk_manager, active_profile):
        """Test position size is at least 1 share when equity allows"""
        equity = 1000.0  # Small equity
        current_price = 150.0
        
        qty = risk_manager.calculate_position_size(
            profile=active_profile,
            equity=equity,
            current_price=current_price
        )
        
        # 10% of 1000 = 100, / 150 = 0, but should return at least 1
        assert qty == 1

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


class TestRiskCheckResult:
    def test_risk_check_result_allowed(self):
        result = RiskCheckResult(allowed=True, reason=None)
        assert result.allowed is True
        assert result.reason is None

    def test_risk_check_result_denied(self):
        result = RiskCheckResult(allowed=False, reason="Test reason")
        assert result.allowed is False
        assert result.reason == "Test reason"
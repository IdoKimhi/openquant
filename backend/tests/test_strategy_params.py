import pytest
from app.schemas import SMACrossoverParams, RSIReversionParams, MomentumBreakoutParams, StrategyProfileCreate
from pydantic import ValidationError


def test_sma_crossover_validates_params():
    # Valid
    SMACrossoverParams(fast_period=10, slow_period=30, position_size_pct=0.1)
    
    # Invalid: fast >= slow
    with pytest.raises(ValidationError):
        SMACrossoverParams(fast_period=30, slow_period=10)
    
    # Invalid: negative
    with pytest.raises(ValidationError):
        SMACrossoverParams(fast_period=-1, slow_period=30)
    
    # Invalid: position_size_pct > 1
    with pytest.raises(ValidationError):
        SMACrossoverParams(fast_period=10, slow_period=30, position_size_pct=1.5)


def test_rsi_reversion_validates_params():
    # Valid
    RSIReversionParams(period=14, oversold=30, overbought=70, position_size_pct=0.1)
    
    # Invalid: oversold >= overbought
    with pytest.raises(ValidationError):
        RSIReversionParams(period=14, oversold=70, overbought=30)
    
    # Invalid: oversold out of range
    with pytest.raises(ValidationError):
        RSIReversionParams(period=14, oversold=0, overbought=70)
    
    # Invalid: overbought out of range
    with pytest.raises(ValidationError):
        RSIReversionParams(period=14, oversold=30, overbought=101)


def test_momentum_breakout_validates_params():
    # Valid
    MomentumBreakoutParams(lookback=20, position_size_pct=0.1)
    
    # Invalid: lookback < 2
    with pytest.raises(ValidationError):
        MomentumBreakoutParams(lookback=1, position_size_pct=0.1)


def test_strategy_profile_create_validates():
    # Valid
    StrategyProfileCreate(
        name="Test",
        strategy_type="sma_crossover",
        parameters={"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        risk_max_position_pct=0.1,
        risk_max_daily_loss_pct=0.05,
        risk_max_concurrent_positions=5,
        symbols=["AAPL"]
    )
    
    # Invalid: empty symbols
    with pytest.raises(ValidationError):
        StrategyProfileCreate(
            name="Test",
            strategy_type="sma_crossover",
            parameters={"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
            risk_max_position_pct=0.1,
            risk_max_daily_loss_pct=0.05,
            risk_max_concurrent_positions=5,
            symbols=[]
        )
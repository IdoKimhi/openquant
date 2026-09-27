from app.bot.strategies import STRATEGY_REGISTRY
from app.models import StrategyProfile
from app.bot.strategies.base import Signal


async def run_strategy(profile: StrategyProfile, alpaca) -> list[Signal]:
    """Run the strategy for a given profile and return signals."""
    strategy_class = STRATEGY_REGISTRY.get(profile.strategy_type.value)
    if not strategy_class:
        raise ValueError(f"Unknown strategy type: {profile.strategy_type.value}")
    
    strategy = strategy_class(profile.parameters)
    signals = await strategy.generate_signals(profile.symbols, profile.parameters, alpaca)
    return signals
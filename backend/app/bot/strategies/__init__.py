from app.bot.strategies.base import BaseStrategy
from app.bot.strategies.sma_crossover import SMACrossoverStrategy
from app.bot.strategies.rsi_reversion import RSIReversionStrategy
from app.bot.strategies.momentum_breakout import MomentumBreakoutStrategy

STRATEGY_REGISTRY = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi_reversion": RSIReversionStrategy,
    "momentum_breakout": MomentumBreakoutStrategy,
}


def get_strategy(strategy_type: str) -> BaseStrategy:
    if strategy_type not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy type: {strategy_type}")
    return STRATEGY_REGISTRY[strategy_type]
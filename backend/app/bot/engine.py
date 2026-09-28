from app.bot.strategies import STRATEGY_REGISTRY
from app.models import StrategyProfile
from app.bot.strategies.base import Signal


async def run_strategy(
    profile: StrategyProfile,
    alpaca,
    investable_equity: float | None = None,
) -> list[Signal]:
    """Run the strategy for a given profile and return signals.

    `investable_equity` is the account equity scaled by
    bot_config.capital_allocation_pct, and it is threaded through rather than
    left to the strategy so every entry in the registry sizes positions the
    same way. Omitting it sizes against the full account, which is what the
    pre-issue-#6 behaviour was.
    """
    strategy_class = STRATEGY_REGISTRY.get(profile.strategy_type.value)
    if not strategy_class:
        raise ValueError(f"Unknown strategy type: {profile.strategy_type.value}")

    # Fold the column into the params dict the strategies read.
    #
    # `allow_fractional_shares` is a column because it gates an *order shape*,
    # not a strategy knob, and the API has to accept it on create. The
    # strategies only ever see `profile.parameters`, so without this line the
    # setting would be stored, rendered in the UI, and never reach a sizing
    # decision - a whole-share-only bot with fractional sharing switched on.
    #
    # A copy, not an in-place assignment: `profile.parameters` is a JSON column
    # SQLAlchemy is tracking, and writing to it would mark the row dirty and
    # commit a change nobody made.
    params = dict(profile.parameters or {})
    params["allow_fractional_shares"] = bool(profile.allow_fractional_shares)

    strategy = strategy_class(params)
    signals = await strategy.generate_signals(
        profile.symbols,
        params,
        alpaca,
        investable_equity=investable_equity,
    )
    return signals
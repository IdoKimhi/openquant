from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List
import math

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    order_type: str = "market"
    limit_price: float = None
    estimated_price: float = 0.0  # for position sizing


# A regular US session yields these bars, used to size a lookback window.
BARS_PER_SESSION = {
    TimeFrameUnit.Minute: 390,  # 09:30-16:00 ET
    TimeFrameUnit.Hour: 7,
    TimeFrameUnit.Day: 1,
}

# Calendar days to request per bar of each timeframe. Daily bars only exist on
# trading days (~252/yr, ~1.45 calendar days each), so 1.6 leaves headroom for
# holidays. Intraday bars are padded for overnight and pre-market gaps.
_DAYS_PER_BAR = {
    TimeFrameUnit.Minute: 1 / 390,
    TimeFrameUnit.Hour: 1 / 7,
    TimeFrameUnit.Day: 1.6,
    TimeFrameUnit.Week: 9.0,
}


def lookback_start(timeframe: TimeFrame, limit: int) -> datetime:
    """A UTC start time far enough back to yield at least `limit` bars.

    Alpaca's v2 bars endpoint returns an empty set when only `limit` is given -
    `start` is mandatory. A window that is too narrow is just as useless as no
    window at all, so scale it by timeframe and add a couple of days of slack
    for weekends, holidays and early closes.
    """
    limit = max(1, int(limit))
    unit = timeframe.unit_value
    amount = max(1, int(getattr(timeframe, "amount", 1) or 1))

    days_per_bar = _DAYS_PER_BAR.get(unit, 1.6)
    if unit in (TimeFrameUnit.Minute, TimeFrameUnit.Hour):
        # Only trading minutes/hours exist, so spread across whole sessions
        per_day = BARS_PER_SESSION.get(unit, 1)
        days = math.ceil((limit * amount) / per_day)
    else:
        days = math.ceil(limit * amount * days_per_bar)

    return datetime.now(timezone.utc) - timedelta(days=days + 2)


def bars_request(symbol: str, timeframe: TimeFrame, limit: int) -> StockBarsRequest:
    """Build a bar request that actually returns data.

    Every strategy must go through here. The previous inline requests omitted
    `start`, so Alpaca answered HTTP 200 with an empty payload, every strategy
    hit its "insufficient bars" branch, and the bot never produced a signal.
    """
    return StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=timeframe,
        start=lookback_start(timeframe, limit),
        limit=limit
    )


class BaseStrategy(ABC):
    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    async def generate_signals(
        self,
        symbols: List[str],
        params: dict,
        alpaca: TradingClient,
        investable_equity: float | None = None,
    ) -> List[Signal]:
        """Signals sized against `investable_equity`.

        `investable_equity` is the worker's capital allocation applied to
        account equity (issue #6). It is a parameter rather than something each
        strategy fetches for itself because all three used to call
        `alpaca.get_equity()` independently, which made the allocation setting
        impossible to honour in one strategy and not another. Left as None, a
        strategy falls back to the account's full equity, so the existing tests
        and any direct caller keep working.
        """

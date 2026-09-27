from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List
from alpaca.trading.client import TradingClient


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    order_type: str = "market"
    limit_price: float = None
    estimated_price: float = 0.0  # for position sizing


class BaseStrategy(ABC):
    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    async def generate_signals(self, symbols: List[str], params: dict, alpaca: TradingClient) -> List[Signal]:
        pass
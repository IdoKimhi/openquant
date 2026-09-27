import httpx
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from app.config import get_settings

settings = get_settings()


class AlpacaClient:
    """Paper-only Alpaca API client wrapper.
    
    Enforces paper trading endpoint - ignores any base_url override.
    """
    
    def __init__(self, api_key: str, secret_key: str, base_url: str = None):
        # HARDCODED: paper-only, ignore any passed base_url
        self.base_url = "https://paper-api.alpaca.markets"
        self.trading = TradingClient(api_key, secret_key, paper=True, url_override=self.base_url)
        self.data = StockHistoricalDataClient(api_key, secret_key)

    async def _request(self, method: str, path: str, **kwargs):
        """Internal helper for endpoints not covered by SDK."""
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            resp = await client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()

    async def get_account(self):
        return self.trading.get_account()

    async def get_positions(self):
        return self.trading.get_all_positions()

    async def get_orders(self, status: str = "all", limit: int = 100):
        req = GetOrdersRequest(status=QueryOrderStatus(status), limit=limit)
        return self.trading.get_orders(req)

    async def submit_order(self, symbol: str, qty: float, side: str, order_type: str = "market", limit_price: float = None):
        if order_type == "market":
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide(side), time_in_force=TimeInForce.DAY)
        else:
            req = LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide(side), limit_price=limit_price, time_in_force=TimeInForce.DAY)
        return self.trading.submit_order(req)

    async def cancel_all_orders(self):
        return self.trading.cancel_orders()

    async def close_all_positions(self):
        return self.trading.close_all_positions()

    async def get_clock(self):
        return self.trading.get_clock()

    async def get_day_pl(self):
        account = await self.get_account()
        return float(account.equity) - float(account.last_equity)

    async def get_equity(self):
        account = await self.get_account()
        return float(account.equity)
    
    async def get_latest_quote(self, symbol: str):
        """Get latest quote for a symbol"""
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data.get_stock_latest_quote(req)
        return quotes[symbol]

    async def get_bars(self, symbols: list[str], timeframe: TimeFrame, limit: int):
        req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=timeframe, limit=limit)
        return self.data.get_stock_bars(req)
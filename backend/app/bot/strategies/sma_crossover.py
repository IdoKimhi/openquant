from typing import List
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal


class SMACrossoverStrategy(BaseStrategy):
    async def generate_signals(self, symbols: List[str], params: dict, alpaca) -> List[Signal]:
        fast_period = params.get("fast_period", 10)
        slow_period = params.get("slow_period", 30)
        position_size_pct = params.get("position_size_pct", 0.10)
        
        # Get account equity for position sizing
        equity = await alpaca.get_equity()
        
        signals = []
        
        for symbol in symbols:
            try:
                # Fetch bars for SMA calculation
                req = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Minute,
                    limit=max(fast_period, slow_period) + 5
                )
                bars = alpaca.data.get_stock_bars(req)
                
                if symbol not in bars.data or len(bars.data[symbol]) < slow_period:
                    continue
                
                closes = [bar.close for bar in bars.data[symbol]]
                
                # Calculate SMAs
                fast_sma = sum(closes[-fast_period:]) / fast_period
                slow_sma = sum(closes[-slow_period:]) / slow_period
                prev_fast_sma = sum(closes[-fast_period-1:-1]) / fast_period
                prev_slow_sma = sum(closes[-slow_period-1:-1]) / slow_period
                
                current_price = closes[-1]
                
                # Check for crossover
                if prev_fast_sma <= prev_slow_sma and fast_sma > slow_sma:
                    # Golden cross - BUY
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif prev_fast_sma >= prev_slow_sma and fast_sma < slow_sma:
                    # Death cross - SELL (flatten position)
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="sell",
                            qty=qty,
                            estimated_price=current_price
                        ))
                        
            except Exception as e:
                # Log error but continue with other symbols
                print(f"Error generating SMA signal for {symbol}: {e}")
                continue
        
        return signals
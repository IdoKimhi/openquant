from typing import List
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal, bars_request


class MomentumBreakoutStrategy(BaseStrategy):
    async def generate_signals(self, symbols: List[str], params: dict, alpaca) -> List[Signal]:
        lookback = params.get("lookback", 20)
        position_size_pct = params.get("position_size_pct", 0.10)
        
        # Get account equity for position sizing
        equity = await alpaca.get_equity()
        
        signals = []
        
        for symbol in symbols:
            try:
                # Fetch daily bars for breakout calculation
                req = bars_request(
                    symbol,
                    TimeFrame.Day,
                    limit=lookback + 1
                )
                bars = alpaca.data.get_stock_bars(req)
                
                if symbol not in bars.data or len(bars.data[symbol]) < lookback + 1:
                    continue
                
                bars_list = bars.data[symbol]
                highs = [bar.high for bar in bars_list[:-1]]  # Exclude current day
                lows = [bar.low for bar in bars_list[:-1]]
                current_price = bars_list[-1].close
                
                max_high = max(highs[-lookback:])
                min_low = min(lows[-lookback:])
                
                # Check for breakout
                if current_price > max_high:
                    # Breakout above resistance - BUY
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif current_price < min_low:
                    # Breakdown below support - SELL (flatten position)
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="sell",
                            qty=qty,
                            estimated_price=current_price
                        ))
                        
            except Exception as e:
                print(f"Error generating Momentum signal for {symbol}: {e}")
                continue
        
        return signals
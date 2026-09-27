from typing import List
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal


class RSIReversionStrategy(BaseStrategy):
    async def generate_signals(self, symbols: List[str], params: dict, alpaca) -> List[Signal]:
        period = params.get("period", 14)
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        position_size_pct = params.get("position_size_pct", 0.10)
        
        # Get account equity for position sizing
        equity = await alpaca.get_equity()
        
        signals = []
        
        for symbol in symbols:
            try:
                # Fetch bars for RSI calculation
                req = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Minute,
                    limit=period + 5
                )
                bars = alpaca.data.get_stock_bars(req)
                
                if symbol not in bars.data or len(bars.data[symbol]) < period + 1:
                    continue
                
                closes = [bar.close for bar in bars.data[symbol]]
                
                # Calculate RSI
                gains = []
                losses = []
                for i in range(1, len(closes)):
                    change = closes[i] - closes[i-1]
                    if change > 0:
                        gains.append(change)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(change))
                
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses[-period:]) / period
                
                if avg_loss == 0:
                    rsi = 100
                else:
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                
                current_price = closes[-1]
                
                # Check RSI levels
                if rsi < oversold:
                    # Oversold - BUY
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif rsi > overbought:
                    # Overbought - SELL (flatten position)
                    qty = int((equity * position_size_pct) / current_price)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="sell",
                            qty=qty,
                            estimated_price=current_price
                        ))
                        
            except Exception as e:
                print(f"Error generating RSI signal for {symbol}: {e}")
                continue
        
        return signals
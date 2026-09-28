from typing import List
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal, bars_request
from app.bot.risk import size_qty


class MomentumBreakoutStrategy(BaseStrategy):
    async def generate_signals(
        self,
        symbols: List[str],
        params: dict,
        alpaca,
        investable_equity: float | None = None,
    ) -> List[Signal]:
        lookback = params.get("lookback", 20)
        position_size_pct = params.get("position_size_pct", 0.10)
        
        # The sizing base, and how to round it.
        #
        # `investable_equity` arrives already scaled by the operator's
        # capital allocation (issue #6). All three strategies used to call
        # `alpaca.get_equity()` for themselves, which meant the setting
        # would have to be honoured in each file independently - and the
        # fallback is kept so a direct caller and the existing tests still
        # size against the whole account.
        equity = investable_equity if investable_equity is not None else await alpaca.get_equity()
        # Fractional sizing is opt-in per profile (issue #2): truncating to
        # whole shares strands up to one share per position, which on a
        # $700 name is $700 a signal. size_qty owns the rounding so the
        # risk limits and the strategies cannot disagree about a size.
        allow_fractional = bool(params.get("allow_fractional_shares", False))
        budget = equity * position_size_pct
        
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
                    qty = size_qty(budget, current_price, allow_fractional)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif current_price < min_low:
                    # Breakdown below support - SELL (flatten position)
                    qty = size_qty(budget, current_price, allow_fractional)
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
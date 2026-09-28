from typing import List
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal, bars_request
from app.bot.risk import size_qty


class SMACrossoverStrategy(BaseStrategy):
    async def generate_signals(
        self,
        symbols: List[str],
        params: dict,
        alpaca,
        investable_equity: float | None = None,
    ) -> List[Signal]:
        fast_period = params.get("fast_period", 10)
        slow_period = params.get("slow_period", 30)
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
                # Fetch bars for SMA calculation
                req = bars_request(
                    symbol,
                    TimeFrame.Minute,
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
                    qty = size_qty(budget, current_price, allow_fractional)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif prev_fast_sma >= prev_slow_sma and fast_sma < slow_sma:
                    # Death cross - SELL (flatten position)
                    qty = size_qty(budget, current_price, allow_fractional)
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
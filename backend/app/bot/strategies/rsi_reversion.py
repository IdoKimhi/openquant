from typing import List
from alpaca.data.timeframe import TimeFrame
from app.bot.strategies.base import BaseStrategy, Signal, bars_request
from app.bot.risk import size_qty


class RSIReversionStrategy(BaseStrategy):
    async def generate_signals(
        self,
        symbols: List[str],
        params: dict,
        alpaca,
        investable_equity: float | None = None,
    ) -> List[Signal]:
        period = params.get("period", 14)
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
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
                # Fetch bars for RSI calculation
                req = bars_request(
                    symbol,
                    TimeFrame.Minute,
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
                    qty = size_qty(budget, current_price, allow_fractional)
                    if qty > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            estimated_price=current_price
                        ))
                elif rsi > overbought:
                    # Overbought - SELL (flatten position)
                    # The qty below is a *budget* figure, not the position.
                    # A strategy cannot know the holding size - generate_signals
                    # is given symbol strings and never sees a position - so it
                    # sizes the exit the only way it can, and BotWorker clamps it
                    # to what is actually held before the order is sent. Do not
                    # read this as the amount that will be sold, and do not try to
                    # make it correct here: see issue #9 and test_sell_quantity.py.
                    qty = size_qty(budget, current_price, allow_fractional)
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
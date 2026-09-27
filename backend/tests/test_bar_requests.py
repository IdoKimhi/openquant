# Tests for bar-request construction.
#
# Alpaca's v2 bars endpoint returns an EMPTY set unless the request carries a
# `start`. A limit-only request silently yields no data, so every strategy
# skipped every symbol and the bot could never produce a signal. These tests
# pin that contract using a fake client that reproduces the real behaviour,
# which the previous mocks (always returning bars) could not detect.

import math
from datetime import datetime, timedelta, timezone

import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from app.bot.strategies.base import bars_request, lookback_start, BARS_PER_SESSION
from app.bot.strategies.sma_crossover import SMACrossoverStrategy
from app.bot.strategies.rsi_reversion import RSIReversionStrategy
from app.bot.strategies.momentum_breakout import MomentumBreakoutStrategy


def _bar(close):
    b = type("Bar", (), {})()
    b.close = close
    b.open = close
    b.high = close + 1
    b.low = close - 1
    return b


def declining_then_spike(n=39, start=200.0, end=100.0, spike=250.0):
    """Falling price that jumps on the final bar.

    Produces a golden cross for the SMA strategy (fast was below slow, now
    above) and a breakout above prior highs for momentum. A purely linear
    series has no crossover at all, so it can never trigger either.
    """
    step = (end - start) / (n - 1)
    return [_bar(start + i * step) for i in range(n)] + [_bar(spike)]


def declining(n=40, start=200.0, end=100.0):
    """Monotonic decline -> RSI pins to 0 (oversold) -> reversion buy."""
    step = (end - start) / (n - 1)
    return [_bar(start + i * step) for i in range(n)]


def rising_then_spike(n=39, start=100.0, end=150.0, spike=250.0):
    """Rising then a jump above prior highs -> momentum breakout buy."""
    step = (end - start) / (n - 1)
    return [_bar(start + i * step) for i in range(n)] + [_bar(spike)]


def rising_then_crash(n=39, start=100.0, end=200.0, crash=50.0):
    """Rising price that collapses on the final bar -> SMA death cross."""
    step = (end - start) / (n - 1)
    return [_bar(start + i * step) for i in range(n)] + [_bar(crash)]


def utcnow_naive():
    """The SDK strips tzinfo from request datetimes, so requests are naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeStockData:
    """Mimics Alpaca: returns data only when `start` is present."""

    def __init__(self, series_factory):
        self.series_factory = series_factory
        self.requests = []

    def get_stock_bars(self, req):
        self.requests.append(req)
        symbols = req.symbol_or_symbols
        if isinstance(symbols, str):
            symbols = [symbols]
        if req.start is None:
            # The real API's behaviour: HTTP 200 with an empty payload
            return type("BarSet", (), {"data": {}})()
        return type("BarSet", (), {"data": {s: list(self.series_factory()) for s in symbols}})()


class FakeAlpaca:
    def __init__(self, data):
        self.data = data
        self.equity = 100000.0

    async def get_equity(self):
        return self.equity


class TestLookbackStart:
    def test_minute_lookback_covers_requested_bars(self):
        # A 390-minute session, so 800 minute bars needs >2 sessions
        start = lookback_start(TimeFrame.Minute, 800)
        days = (datetime.now(timezone.utc) - start).days
        assert days >= 3

    def test_daily_lookback_covers_requested_bars(self):
        # ~252 trading days/yr -> 1.6 calendar days per bar is a safe ratio
        start = lookback_start(TimeFrame.Day, 60)
        days = (datetime.now(timezone.utc) - start).days
        assert days >= 60 * 1.5

    def test_lookback_is_in_the_past(self):
        start = lookback_start(TimeFrame.Day, 30)
        assert start.tzinfo is not None, "built timezone-aware"
        assert start < datetime.now(timezone.utc)

    def test_hourly_lookback_covers_requested_bars(self):
        start = lookback_start(TimeFrame.Hour, 40)
        days = (datetime.now(timezone.utc) - start).days
        assert days >= 6  # 7 hourly bars per session

    def test_multi_unit_timeframe_scales(self):
        start = lookback_start(TimeFrame(5, TimeFrameUnit.Minute), 400)
        days = (datetime.now(timezone.utc) - start).days
        assert days >= 6


class TestBarsRequest:
    def test_request_always_carries_a_start(self):
        req = bars_request("AAPL", TimeFrame.Minute, 35)
        assert req.start is not None

    def test_request_preserves_symbol_timeframe_and_limit(self):
        req = bars_request("TSLA", TimeFrame.Day, 21)
        assert req.symbol_or_symbols == ["TSLA"]
        # TimeFrame has no __eq__, so compare its components
        assert req.timeframe.unit_value == TimeFrame.Day.unit_value
        assert req.timeframe.amount == TimeFrame.Day.amount
        assert req.limit == 21

    @pytest.mark.parametrize("tf,limit", [
        (TimeFrame.Minute, 35),
        (TimeFrame.Minute, 400),
        (TimeFrame.Hour, 40),
        (TimeFrame.Day, 21),
        (TimeFrame.Day, 200),
    ])
    def test_window_is_wide_enough_for_the_limit(self, tf, limit):
        """The window must span enough sessions to actually hold `limit` bars."""
        start = bars_request("X", tf, limit).start
        per_day = BARS_PER_SESSION[tf.unit_value]
        # Enough whole sessions to hold the bars, plus the 2-day weekend slack
        # the implementation adds on top.
        required_days = math.ceil(limit / per_day)
        have_days = (utcnow_naive() - start).total_seconds() / 86400
        assert have_days >= required_days
        assert have_days >= required_days + 2, "no slack for weekends/holidays"


class TestStrategiesRequestBarsWithStart:
    """End-to-end: a strategy must produce signals against an Alpaca-faithful client."""

    @pytest.mark.asyncio
    async def test_sma_crossover_generates_a_buy_signal(self):
        data = FakeStockData(declining_then_spike)
        strat = SMACrossoverStrategy({"fast_period": 5, "slow_period": 20, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))

        assert data.requests, "strategy never requested bars"
        assert all(r.start is not None for r in data.requests), "a bar request omitted start"
        assert len(signals) == 1, f"expected a signal, got {signals}"
        s = signals[0]
        assert s.symbol == "AAPL"
        assert s.side == "buy", "a golden cross must produce a buy"
        assert s.qty > 0
        assert s.estimated_price > 0

    @pytest.mark.asyncio
    async def test_sma_crossover_generates_a_sell_on_death_cross(self):
        """The mirror case, so a buy can never pass by construction alone."""
        data = FakeStockData(rising_then_crash)
        strat = SMACrossoverStrategy({"fast_period": 5, "slow_period": 20, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))

        assert len(signals) == 1, f"expected a signal, got {signals}"
        assert signals[0].side == "sell"

    @pytest.mark.asyncio
    async def test_rsi_reversion_buys_when_oversold(self):
        data = FakeStockData(declining)
        strat = RSIReversionStrategy({"period": 14, "oversold": 30, "overbought": 70, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))

        assert data.requests
        assert all(r.start is not None for r in data.requests)
        assert len(signals) == 1, f"expected a signal, got {signals}"
        assert signals[0].side == "buy", "a monotonic decline is oversold"

    @pytest.mark.asyncio
    async def test_rsi_reversion_sells_when_overbought(self):
        data = FakeStockData(lambda: declining(start=100.0, end=200.0))
        strat = RSIReversionStrategy({"period": 14, "oversold": 30, "overbought": 70, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))

        assert len(signals) == 1, f"expected a signal, got {signals}"
        assert signals[0].side == "sell", "a monotonic rise is overbought"

    @pytest.mark.asyncio
    async def test_momentum_breakout_buys_on_breakout(self):
        data = FakeStockData(rising_then_spike)
        strat = MomentumBreakoutStrategy({"lookback": 20, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))

        assert data.requests
        assert all(r.start is not None for r in data.requests)
        assert len(signals) == 1, f"expected a signal, got {signals}"
        assert signals[0].side == "buy", "a close above prior highs is a breakout"

    @pytest.mark.asyncio
    async def test_no_signal_when_data_is_genuinely_unavailable(self):
        """Empty data must still yield no signals - the fix is not 'always trade'."""
        class AlwaysEmpty(FakeStockData):
            def get_stock_bars(self, req):
                self.requests.append(req)
                return type("BarSet", (), {"data": {}})()

        data = AlwaysEmpty(declining_then_spike)
        strat = SMACrossoverStrategy({"fast_period": 5, "slow_period": 20, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))
        assert signals == []

    @pytest.mark.asyncio
    async def test_flat_market_produces_no_signal(self):
        """No crossover, no signal - guards against trading on noise."""
        data = FakeStockData(lambda: [_bar(150.0)] * 40)
        strat = SMACrossoverStrategy({"fast_period": 5, "slow_period": 20, "position_size_pct": 0.10})
        signals = await strat.generate_signals(["AAPL"], strat.params, FakeAlpaca(data))
        assert signals == []

    @pytest.mark.asyncio
    async def test_each_symbol_gets_its_own_request(self):
        data = FakeStockData(declining_then_spike)
        strat = SMACrossoverStrategy({"fast_period": 5, "slow_period": 20, "position_size_pct": 0.10})
        await strat.generate_signals(["AAPL", "TSLA", "GOOG"], strat.params, FakeAlpaca(data))

        got = {r.symbol_or_symbols[0] for r in data.requests}
        assert got == {"AAPL", "TSLA", "GOOG"}

# Tests for issue #3: bot_config.market_hours_only is now read by the worker.
#
# The flag was stored, displayed, and consulted by nothing (gotcha 17). The
# worker gated every cycle on `clock.is_open` alone, and Alpaca reports that
# true from 04:00 to 20:00 ET, so a cycle at 19:30 ET placed orders that sat
# overnight with no liquidity behind them.
#
# Two things this file is careful about, because the report that prompted the
# fix was itself wrong in an instructive way. Issue #3 said "bot traded at
# 19:30-19:50 ET". The trade_logs rows behind that claim hold 19:30-19:50
# *UTC*, which is 15:30-15:50 EDT - the last half hour of the regular session,
# ten minutes before the close. The bot did not trade after hours; a naive
# UTC timestamp was rendered as browser-local time (issue #4) and read as an
# after-hours trade. So:
#
#   * the gate is asserted against a clock we control, in ET, not against the
#     string the UI happened to print; and
#   * the log line is asserted, because a worker that says "Market is closed"
#     for every non-regular session is exactly what made the two conditions
#     indistinguishable.

import asyncio
import logging
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus

from app.bot.market_hours import (
    MARKET_TZ, SESSION_AFTER_HOURS, SESSION_CLOSED, SESSION_PRE_MARKET,
    SESSION_REGULAR, market_session_state, skip_reason, trading_allowed,
)
from app.db import get_session_local
from app.models import (
    ApiCredentials, BotConfig, StrategyProfile, StrategyType, TradeLog,
)
from app.security import encrypt

NY = ZoneInfo("America/New_York")


def at(hour: int, minute: int = 0, day: int = 28, month: int = 9) -> datetime:
    """A UTC instant, expressed the way a broker timestamp arrives."""
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def et(hour: int, minute: int = 0, day: int = 28, month: int = 9) -> datetime:
    """A wall-clock time in the market zone, as a UTC instant."""
    return datetime(2026, month, day, hour, minute, tzinfo=NY).astimezone(timezone.utc)


def clock(is_open: bool, timestamp: datetime) -> MagicMock:
    c = MagicMock()
    c.is_open = is_open
    c.timestamp = timestamp
    c.next_open = timestamp
    c.next_close = timestamp
    return c


class TestSessionClassification:
    """Which session a moment falls in."""

    @pytest.mark.parametrize("hour,minute", [
        (4, 0),   # extended hours open
        (8, 0),
        (9, 0),
        (9, 29),  # one minute before the open, still pre-market
    ])
    def test_before_the_open_is_pre_market(self, hour, minute):
        assert market_session_state(clock(True, et(hour, minute))) == SESSION_PRE_MARKET

    @pytest.mark.parametrize("hour,minute", [(9, 30), (12, 0), (15, 55), (15, 59)])
    def test_the_regular_session_includes_the_closing_auction(self, hour, minute):
        assert market_session_state(clock(True, et(hour, minute))) == SESSION_REGULAR

    @pytest.mark.parametrize("hour,minute", [(16, 0), (16, 1), (19, 30), (19, 59)])
    def test_after_the_close_is_after_hours(self, hour, minute):
        assert market_session_state(clock(True, et(hour, minute))) == SESSION_AFTER_HOURS

    def test_the_broker_closing_is_the_authority(self):
        """16:05 ET is after-hours by the wall clock, but the broker says the
        market is shut - a holiday, or a day the exchange was closed. The
        broker wins; a holiday calendar is not derivable from a weekday."""
        assert market_session_state(clock(False, et(16, 5))) == SESSION_CLOSED

    def test_weekend_is_closed_even_if_the_clock_claims_open(self):
        # 2026-10-03 is a Saturday.
        saturday = et(12, 0, day=3, month=10)
        assert saturday.astimezone(NY).weekday() == 5
        assert market_session_state(clock(True, saturday)) == SESSION_CLOSED

    def test_early_close_day_is_closed_after_the_early_close(self):
        """A 13:00 half-day close: 12:00 is still the regular session, 13:30
        is closed. Fixed 16:00 bounds would call 13:30 'after hours' and, with
        market_hours_only off, would have traded it."""
        assert market_session_state(clock(True, et(12, 0))) == SESSION_REGULAR
        assert market_session_state(clock(False, et(13, 30))) == SESSION_CLOSED

    def test_market_timezone_is_the_configured_one(self):
        """Not hardcoded: the session has to be judged in the same zone the
        cron is written in, or a deployment on another zone gates on the wrong
        clock (gotcha 8, one layer down)."""
        from app.config import get_settings
        assert MARKET_TZ.key == get_settings().bot_timezone

    def test_an_explicit_now_overrides_the_clock_timestamp(self):
        """Both inputs are there so a test - or a caller holding a fresher
        instant - can be exact, and so neither is load-bearing by accident."""
        assert market_session_state(clock(True, et(12, 0)), now=et(19, 30)) == SESSION_AFTER_HOURS
        assert market_session_state(clock(True, et(19, 30)), now=et(12, 0)) == SESSION_REGULAR


class TestTradingAllowed:
    def test_closed_is_never_tradeable(self):
        for flag in (True, False):
            assert trading_allowed(SESSION_CLOSED, flag) is False

    def test_market_hours_only_permits_exactly_the_regular_session(self):
        assert trading_allowed(SESSION_REGULAR, True) is True
        assert trading_allowed(SESSION_PRE_MARKET, True) is False
        assert trading_allowed(SESSION_AFTER_HOURS, True) is False

    def test_turning_the_flag_off_permits_extended_hours(self):
        """Opt-in, and opt-in means opt-in: with the flag off the bot really
        does place pre-market and after-hours orders. That is the point of the
        control, and it is why the default stays on."""
        assert trading_allowed(SESSION_PRE_MARKET, False) is True
        assert trading_allowed(SESSION_AFTER_HOURS, False) is True

    def test_every_state_is_covered(self):
        from app.bot.market_hours import SESSION_STATES
        for state in SESSION_STATES:
            for flag in (True, False):
                assert isinstance(trading_allowed(state, flag), bool)
                assert isinstance(skip_reason(state, flag), str)


class TestSkipReasonSaysWhichWindow:
    """The log line is the diagnostic. A worker that logs one message for
    every non-regular session cannot be told apart from a broken one."""

    def test_names_extended_hours_rather_than_just_saying_closed(self):
        assert "After-hours" in skip_reason(SESSION_AFTER_HOURS, True)
        assert "Pre-market" in skip_reason(SESSION_PRE_MARKET, True)

    def test_a_weekend_is_reported_as_closed(self):
        assert "closed" in skip_reason(SESSION_CLOSED, True)

    def test_an_allowed_session_has_no_reason_to_log(self):
        assert skip_reason(SESSION_REGULAR, True) == ""
        assert skip_reason(SESSION_AFTER_HOURS, False) == ""


# --------------------------------------------------------------------------
# The cycle itself.
#
# Everything above is a pure function. A pure function being right does not
# mean the worker calls it, so these drive `_run_trading_cycle_async` for real
# and assert on what reached the broker. Only `submit_order` is stubbed, and it
# returns the SDK types the real one does (a uuid.UUID id, a real Alpaca
# status member) - a convenient string id and a bare status is what hid three
# earlier bugs in this file's neighbourhood.
# --------------------------------------------------------------------------

def seed_profile(session, *, allocation=1.0, market_hours_only=True, is_running=True):
    db = session()
    try:
        db.query(ApiCredentials).delete()
        db.add(ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"), secret_key_encrypted=encrypt("sktest"),
        ))
        db.add(BotConfig(
            id=1, is_running=is_running, market_hours_only=market_hours_only,
            capital_allocation_pct=allocation, active_profile_id=1,
        ))
        profile = StrategyProfile(
            id=1, name="gate", strategy_type=StrategyType.rsi_reversion,
            parameters={"position_size_pct": 0.15}, symbols=["AAPL"],
            risk_max_position_pct=0.15, risk_max_daily_loss_pct=0.05,
            risk_max_concurrent_positions=10, enabled=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    finally:
        db.close()


def fake_account(equity=100000.0):
    account = MagicMock()
    account.equity = equity
    account.last_equity = equity
    account.cash = equity
    account.buying_power = equity
    account.status = "ACTIVE"
    account.daytrade_count = 0
    return account


def run_cycle(clock_at: datetime, *, allocation=1.0, market_hours_only=True):
    """One real trading cycle, with only the broker's order placement stubbed.

    Returns the AlpacaClient mock so a test can assert on whether an order was
    submitted at all.
    """
    from worker.scheduler import BotWorker

    SessionLocal = get_session_local()
    seed_profile(SessionLocal, allocation=allocation, market_hours_only=market_hours_only)

    db = SessionLocal()
    try:
        worker = BotWorker.__new__(BotWorker)   # no scheduler, no signal handlers
        worker.db = db
        worker.running = True

        alpaca = AsyncMock()
        alpaca.get_clock = AsyncMock(return_value=clock(True, clock_at))
        alpaca.get_account = AsyncMock(return_value=fake_account())
        alpaca.get_positions = AsyncMock(return_value=[])
        alpaca.get_latest_quote = AsyncMock(return_value=MagicMock(
            ask_price=100.0, bid_price=99.9,
        ))
        submitted = AsyncMock(return_value=_filled_order())
        alpaca.submit_order = submitted

        signal = MagicMock()
        signal.symbol = "AAPL"
        signal.side = "buy"
        signal.qty = 10
        signal.order_type = "market"
        signal.limit_price = None
        signal.estimated_price = 100.0

        with patch("worker.scheduler.run_strategy", new=AsyncMock(return_value=[signal])), \
             patch("worker.scheduler.AlpacaClient", return_value=alpaca):
            asyncio.run(worker._run_trading_cycle_async())

        return submitted
    finally:
        db.close()


def _filled_order():
    order = MagicMock()
    order.id = "22222222-2222-2222-2222-222222222222"
    order.status = AlpacaOrderStatus.FILLED
    order.filled_avg_price = 100.05
    order.filled_qty = 10
    return order


class TestTheCycleRespectsTheWindow:
    """The reported bug, at the call site."""

    def test_no_order_after_the_close_when_the_flag_is_on(self):
        submitted = run_cycle(et(19, 30), market_hours_only=True)
        submitted.assert_not_called()

    def test_no_order_before_the_open_when_the_flag_is_on(self):
        submitted = run_cycle(et(7, 0), market_hours_only=True)
        submitted.assert_not_called()

    def test_an_order_is_placed_inside_the_regular_session(self):
        """The control that stops the gate being a bot that never trades.

        15:55 ET is the last cycle the default `*/5 9-16` cron reaches before
        the close, so it is the case that would regress first.
        """
        submitted = run_cycle(et(15, 55), market_hours_only=True)
        submitted.assert_called_once()

    def test_the_flag_off_really_does_permit_extended_hours(self):
        """Otherwise "the flag now works" could also mean "the flag now blocks
        everything"."""
        submitted = run_cycle(et(19, 30), market_hours_only=False)
        submitted.assert_called_once()

    def test_a_closed_market_is_skipped_even_with_the_flag_off(self):
        submitted = run_cycle(et(12, 0), market_hours_only=False)
        # Simulate the broker being shut by flipping is_open, which is the
        # weekend/holiday path and cannot be faked with a wall clock.
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            worker = __import__("worker.scheduler", fromlist=["BotWorker"]).BotWorker.__new__(
                __import__("worker.scheduler", fromlist=["BotWorker"]).BotWorker
            )
            worker.db = db
            worker.running = True
            alpaca = AsyncMock()
            alpaca.get_clock = AsyncMock(return_value=clock(False, et(12, 0)))
            alpaca.submit_order = AsyncMock(return_value=_filled_order())
            with patch("worker.scheduler.run_strategy", new=AsyncMock(return_value=[])), \
                 patch("worker.scheduler.AlpacaClient", return_value=alpaca):
                asyncio.run(worker._run_trading_cycle_async())
        finally:
            db.close()
        alpaca.submit_order.assert_not_called()


class TestTheCycleLogsWhichWindowItSkipped:
    def test_after_hours_skip_is_distinguishable_from_a_closed_market(self, caplog):
        with caplog.at_level(logging.INFO, logger="worker.scheduler"):
            run_cycle(et(19, 30), market_hours_only=True)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "after-hours" in text.lower()
        assert "market_hours_only" in text


class TestNoTradeLogIsWrittenForASkippedCycle:
    def test_a_skipped_after_hours_cycle_leaves_no_rows(self):
        """A refused order and an absent cycle have to look different in the
        Activity Log - 'silence' is the failure mode that already cost this
        project one bug (gotcha 12)."""
        SessionLocal = get_session_local()
        run_cycle(et(19, 30), market_hours_only=True)
        db = SessionLocal()
        try:
            assert db.query(TradeLog).count() == 0
        finally:
            db.close()

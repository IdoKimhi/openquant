# Tests for issue #5: fill price and fill quantity are never recorded.
#
# Every one of the 52 rows in the live trade_logs table has
# filled_price = NULL and filled_qty = NULL, and all 52 are
# status = 'submitted'. Nothing is broken and nothing errors: the worker writes
# the row immediately after `submit_order` returns, and at that moment the
# broker reports the order as `new` or `accepted` with no fill data. The order
# fills milliseconds later, and there is no second code path that ever goes
# back and asks.
#
# The columns have existed the whole time. That is what makes this bug quiet:
# the schema says the app knows what it paid, the UI has nowhere to render it
# because the value is always NULL, and no error is raised anywhere.
#
# Two consequences beyond the missing numbers:
#
#   * `TradeLog.pnl` is still never written, so check_daily_loss's local
#     fallback remains inert (gotcha 7). This work does not fix that - the
#     daily loss limit correctly reads the broker's account now - but fills are
#     the missing half of any per-trade P&L, and the reason it is unreachable
#     is that this data does not exist.
#
#   * A bot that cannot see its own execution prices cannot tell a bad fill
#     from a good one, which is the whole basis of the "only 70% deployed"
#     complaint in issue #2.
#
# The design, and why:
#
#   * Capture at submit time when the broker has already filled. Alpaca
#     frequently returns a filled order straight from `submit_order` for a
#     market order, so this is free and covers most cases.
#   * Otherwise reconcile on a timer. A market order can also come back
#     `accepted` and fill a second later, so the row is updated by a separate
#     job that re-reads open orders from the broker. This is the part that
#     actually closes the gap; capturing at submit alone would fix the
#     reported symptom most of the time and leave the rest.
#
# The reconciliation query is deliberately bounded in both directions. An
# unbounded "every row with NULL filled_price, forever" query walks the whole
# table on every tick, and it also reaches back to orders Alpaca has long since
# expired from the orders API - those would 404 on every single tick, forever.

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus

from app.alpaca_client import AlpacaClient
from app.db import get_session_local
from app.models import OrderStatus, TradeLog


def broker_order(status=AlpacaOrderStatus.NEW, filled_qty=None, filled_avg_price=None):
    """An order shaped like the ones the SDK returns.

    The real types, per gotcha 5b: `id` is a uuid.UUID, `qty` and the fill
    fields are Decimal, `status` is an enum member. A stub returning strings
    and floats is precisely what hid bugs 7-9 for a release.
    """
    order = MagicMock()
    order.id = uuid.UUID("3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6")
    order.symbol = "AAPL"
    order.qty = Decimal("2")
    order.side = "buy"
    order.order_type = "market"
    order.limit_price = None
    order.status = status
    order.filled_qty = None if filled_qty is None else Decimal(str(filled_qty))
    order.filled_avg_price = None if filled_avg_price is None else Decimal(str(filled_avg_price))
    order.submitted_at = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    return order


def log_row(session, **kw):
    defaults = dict(
        symbol="AAPL", side="buy", qty=2.0, order_type="market",
        status=OrderStatus.submitted.value,
        alpaca_order_id="3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6",
        message="Order placed: buy 2 AAPL @ market",
    )
    defaults.update(kw)
    row = TradeLog(**defaults)
    session.add(row)
    session.commit()
    return row


class TestSubmitOrderReportsWhatTheBrokerAlreadyKnows:
    """A market order is often already filled when submit_order returns.

    Alpaca fills a liquid market order in the same round trip most of the
    time, and the response carries filled_avg_price and filled_qty. Writing
    them when they are there is free, and it means the common case never
    depends on the reconciler running.
    """

    def test_submit_response_exposes_the_fill(self):
        """The wrapper's own method, against a fake `self` - the same gotcha
        the AGENTS trading-path notes call out: submit_order is patched onto
        the class, so the instance arrives positionally."""
        client = AlpacaClient.__new__(AlpacaClient)
        client.trading = MagicMock()
        client.trading.submit_order = MagicMock(
            return_value=broker_order(AlpacaOrderStatus.FILLED, 2, 337.41)
        )

        order = asyncio.run(client.submit_order(symbol="AAPL", qty=2, side="buy"))

        assert float(order.filled_avg_price) == pytest.approx(337.41)
        assert float(order.filled_qty) == pytest.approx(2.0)

    def test_get_order_fetches_a_single_order_by_id(self):
        """The reconciler needs to re-read one order by its id. TradingClient
        exposes get_order_by_id; there is no such method on our wrapper, so
        every attempt to reconcile has been a manual SDK call."""
        client = AlpacaClient.__new__(AlpacaClient)
        client.trading = MagicMock()
        client.trading.get_order_by_id = MagicMock(
            return_value=broker_order(AlpacaOrderStatus.FILLED, 2, 337.41)
        )

        order = asyncio.run(client.get_order("3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6"))

        assert float(order.filled_avg_price) == pytest.approx(337.41)
        client.trading.get_order_by_id.assert_called_once()

    def test_a_fractional_limit_order_is_refused_before_it_reaches_the_broker(self):
        """Alpaca supports a fractional qty on market orders only. Sending one
        on a limit order gets rejected by the broker, which is a wasted round
        trip and a confusing "rejected" row in the activity log for what is
        really a configuration error. Refused in-process, with a message that
        says which knob to turn.

        The bot currently only issues market orders, so this is unreachable
        today - it is here because submit_order is the boundary, and the
        boundary is where a broker constraint should be expressed."""
        client = AlpacaClient.__new__(AlpacaClient)
        client.trading = MagicMock()

        with pytest.raises(ValueError, match="fractional"):
            asyncio.run(client.submit_order(
                symbol="AAPL", qty=0.5, side="buy",
                order_type="limit", limit_price=337.0,
            ))

        client.trading.submit_order.assert_not_called()

    def test_a_whole_limit_order_still_goes_through(self):
        """The guard above must not have narrowed normal limit orders."""
        client = AlpacaClient.__new__(AlpacaClient)
        client.trading = MagicMock()
        client.trading.submit_order = MagicMock(
            return_value=broker_order(AlpacaOrderStatus.NEW)
        )

        asyncio.run(client.submit_order(
            symbol="AAPL", qty=2, side="buy", order_type="limit", limit_price=337.0,
        ))

        client.trading.submit_order.assert_called_once()


class TestWorkerCapturesAnImmediateFill:
    """What _process_signal writes when the broker filled during submit."""

    def _run(self, order):
        from app.bot.risk import RiskManager
        from app.bot.strategies.base import Signal
        from app.models import StrategyProfile, StrategyType
        from worker.scheduler import BotWorker

        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            profile = StrategyProfile(
                name="p", strategy_type=StrategyType.sma_crossover,
                parameters={}, symbols=["AAPL"],
                risk_max_position_pct=0.5, risk_max_daily_loss_pct=0.05,
                risk_max_concurrent_positions=5, enabled=True,
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)

            worker = BotWorker()
            worker.db = db
            alpaca = AsyncMock()
            alpaca.submit_order = AsyncMock(return_value=order)

            asyncio.run(worker._process_signal(
                signal=Signal(symbol="AAPL", side="buy", qty=2,
                              order_type="market", estimated_price=337.0),
                profile=profile, alpaca=alpaca,
                investable_equity=10000.0,
                current_positions_count=0, current_position_symbols=set(),
                risk_manager=RiskManager(db), daily_loss_pct=0.0,
            ))
            return db.query(TradeLog).all()
        finally:
            db.close()

    def test_an_order_filled_at_submit_records_its_price_and_quantity(self):
        rows = self._run(broker_order(AlpacaOrderStatus.FILLED, 2, 337.41))

        assert len(rows) == 1
        assert rows[0].status == OrderStatus.filled.value
        assert rows[0].filled_price == pytest.approx(337.41)
        assert rows[0].filled_qty == pytest.approx(2.0)

    def test_an_order_left_open_records_neither(self):
        """Not filled is not the same as filled-at-zero. Writing 0.0 would be
        indistinguishable from a genuine zero-price fill and would make the
        reconciler's job - finding rows that still need filling - impossible."""
        rows = self._run(broker_order(AlpacaOrderStatus.ACCEPTED))

        assert len(rows) == 1
        assert rows[0].status == OrderStatus.submitted.value
        assert rows[0].filled_price is None
        assert rows[0].filled_qty is None

    def test_a_partially_filled_order_records_the_partial(self):
        """partially_filled is a real state with a real fill against it, and
        it is the state most likely to be misread as 'nothing happened'.
        The order is still open, so the reconciler keeps it on its list -
        but the partial is data we have and should not throw away."""
        rows = self._run(broker_order(AlpacaOrderStatus.PARTIALLY_FILLED, 1, 337.30))

        assert len(rows) == 1
        assert rows[0].filled_price == pytest.approx(337.30)
        assert rows[0].filled_qty == pytest.approx(1.0)
        assert rows[0].status == OrderStatus.submitted.value


class TestReconcileFills:
    """The reconciler is what actually closes the gap.

    `submit_order` returning `accepted` is the common case for anything that
    is not an instantly-liquid market order, and it is the case that has left
    every row in the live table with NULL fills.
    """

    def _worker(self):
        from worker.scheduler import BotWorker
        worker = BotWorker()
        worker.db = get_session_local()()
        return worker

    def test_a_later_fill_is_written_back_to_the_row(self):
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            log_row(db)
        finally:
            db.close()

        worker = self._worker()
        alpaca = AsyncMock()
        alpaca.get_order = AsyncMock(
            return_value=broker_order(AlpacaOrderStatus.FILLED, 2, 337.41)
        )
        try:
            asyncio.run(worker._reconcile_fills(alpaca=alpaca))

            row = SessionLocal().query(TradeLog).first()
            assert row.filled_price == pytest.approx(337.41)
            assert row.filled_qty == pytest.approx(2.0)
            assert row.status == OrderStatus.filled.value
        finally:
            worker.db.close()

    def test_a_row_that_is_still_open_is_left_alone(self):
        """Writing zeros into an order that has not filled would destroy the
        very NULL the reconciler searches on, and would report a fill that
        never happened."""
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            log_row(db)
        finally:
            db.close()

        worker = self._worker()
        alpaca = AsyncMock()
        alpaca.get_order = AsyncMock(return_value=broker_order(AlpacaOrderStatus.ACCEPTED))
        try:
            asyncio.run(worker._reconcile_fills(alpaca=alpaca))

            row = SessionLocal().query(TradeLog).first()
            assert row.filled_price is None
            assert row.filled_qty is None
        finally:
            worker.db.close()

    def test_an_order_the_broker_no_longer_has_is_skipped(self):
        """A DAY order that never filled expires; Alpaca's single-order GET
        then 404s, and the orders API only retains a bounded window. An
        unbounded "everything with NULL filled_price" query therefore hits
        permanent 404s on every tick, forever - and if those raise, the whole
        reconciler dies and never fills anything again.

        One order failing must not stop the others from being reconciled."""
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            log_row(db, alpaca_order_id="aaaaaaaa-0000-0000-0000-000000000000")
            log_row(db, alpaca_order_id="3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6")
        finally:
            db.close()

        def get_order(order_id, *a, **kw):
            if str(order_id).startswith("aaaaaaaa"):
                raise APIError("order not found", 404)
            return broker_order(AlpacaOrderStatus.FILLED, 2, 337.41)

        worker = self._worker()
        alpaca = AsyncMock()
        alpaca.get_order = AsyncMock(side_effect=get_order)
        try:
            asyncio.run(worker._reconcile_fills(alpaca=alpaca))

            rows = {r.alpaca_order_id: r for r in SessionLocal().query(TradeLog).all()}
            assert rows["3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6"].filled_qty == pytest.approx(2.0)
            # Untouched, and still eligible for a future attempt.
            assert rows["aaaaaaaa-0000-0000-0000-000000000000"].filled_qty is None
        finally:
            worker.db.close()

    def test_only_recent_unfilled_rows_are_considered(self):
        """Bounded in age, and the bound is the point.

        Alpaca's order history does not retain an order indefinitely. Left
        unbounded, this query re-checks every unfilled row the table has ever
        accumulated on every tick, and every one of the old ones is a guaranteed
        404 - the worker's log fills with errors that are actually normal, and
        the real failures become invisible among them."""
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            log_row(db)
            db.add(TradeLog(
                symbol="MSFT", side="buy", qty=1, order_type="market",
                status=OrderStatus.submitted.value,
                alpaca_order_id="bbbbbbbb-0000-0000-0000-000000000000",
                message="ancient", timestamp=datetime.utcnow() - timedelta(days=30),
            ))
            db.commit()
        finally:
            db.close()

        worker = self._worker()
        alpaca = AsyncMock()
        alpaca.get_order = AsyncMock(
            return_value=broker_order(AlpacaOrderStatus.FILLED, 2, 337.41)
        )
        try:
            asyncio.run(worker._reconcile_fills(alpaca=alpaca))

            # The recent row is the only one the broker was asked about.
            assert [str(c.args[0]) for c in alpaca.get_order.call_args_list] == [
                "3f1c0a9e-1b2c-4d5e-8f90-a1b2c3d4e5f6"
            ]
        finally:
            worker.db.close()

    def test_a_row_with_no_broker_id_is_not_queried(self):
        """An order that failed to place has no id. There is nothing to ask
        the broker, and calling get_order(None) is a guaranteed error."""
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            log_row(db, alpaca_order_id=None, status=OrderStatus.error.value)
        finally:
            db.close()

        worker = self._worker()
        alpaca = AsyncMock()
        try:
            asyncio.run(worker._reconcile_fills(alpaca=alpaca))
            alpaca.get_order.assert_not_called()
        finally:
            worker.db.close()

    def test_the_reconciler_is_registered_as_a_job(self):
        """A method nothing schedules is a method that never runs, which is
        exactly the shape the original bug had: the fill data was never
        missing from a code path, it was missing from a *schedule*.

        Driven through the real `start()`, because asserting the method exists
        is not the claim - the claim is that the worker's own schedule calls
        it. A test that called `add_job` itself would pass against a build
        where `start()` had no such line, which is the failure mode
        `test_activate_profile` fell into for a whole release.

        Sync entry point, per gotcha 9: APScheduler runs jobs in a plain thread
        and does not await coroutines, so an `async def` job would create a
        coroutine and discard it."""
        from app.models import BotConfig
        from worker.scheduler import BotWorker

        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            db.add(BotConfig(id=1, is_running=True, schedule_cron="*/5 9-16 * * MON-FRI"))
            db.commit()
        finally:
            db.close()

        worker = BotWorker()
        worker.db = SessionLocal()
        try:
            worker.start()
            assert worker.scheduler.get_job("reconcile_fills") is not None, (
                "the worker never scheduled the fill reconciler"
            )
            assert not asyncio.iscoroutinefunction(worker._reconcile_fills_job)
        finally:
            worker.stop()
            worker.db.close()

    def test_the_reconciler_runs_even_when_the_bot_is_stopped(self):
        """An order placed on the last cycle before a stop still fills, and
        the row still has to learn what it cost. Gating the sweep on
        `is_running` would skip exactly the orders most likely to still be in
        flight - which is nearly all of them, since a fill takes milliseconds
        and the sweep runs a minute later."""
        from app.models import BotConfig
        from worker.scheduler import BotWorker

        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            db.add(BotConfig(id=1, is_running=False))
            db.commit()
        finally:
            db.close()

        worker = BotWorker()
        worker.db = SessionLocal()
        try:
            worker.start()
            assert worker.scheduler.get_job("trading_cycle") is None
            assert worker.scheduler.get_job("reconcile_fills") is not None
        finally:
            worker.stop()
            worker.db.close()

    def test_the_cycle_survives_a_reconciler_failure(self):
        """A read-only bookkeeping job must not be able to take down the
        worker. It runs on its own timer, so an escaping exception would take
        out the trading cycle and the config poller with it."""
        from worker.scheduler import BotWorker

        worker = BotWorker()
        try:
            SessionLocal = get_session_local()
            db = SessionLocal()
            try:
                log_row(db)
            finally:
                db.close()

            # An Alpaca client whose every call raises.
            alpaca = AsyncMock()
            alpaca.get_order = AsyncMock(side_effect=RuntimeError("network down"))

            asyncio.run(worker._reconcile_fills(alpaca=alpaca))  # must not raise
        finally:
            worker.db.close()

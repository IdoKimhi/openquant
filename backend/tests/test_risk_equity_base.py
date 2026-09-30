# The base `validate_order` is given, from the worker's call site.
#
# `RiskManager.investable_equity` has a docstring promising that the capital
# allocation applies to "position sizing and every risk limit", and
# `test_capital_allocation.py` proves the *strategy* sizes against the
# investable base. Neither of those could see the other half: the worker
# passed raw account equity into `validate_order`, so the position cap was
# measured against the whole account while the positions themselves were sized
# against 80% of it. The cap ended up 25% looser than configured.
#
# This file pins the call site itself. The unit tests in test_risk.py pin the
# behaviour of the function *given* a base, which is the difference that
# matters: a function that behaves correctly for any input it is handed can
# still be handed the wrong input, and that is exactly what happened.
#
# `worker/scheduler.py` had no tests at all before this, and the eleven trading
# bugs listed in AGENTS.md were all invisible to the suite. The lesson recorded
# there is "do not add a feature to the trading path without a check that runs
# against real market data" - and the narrower form of the same lesson is that
# a feature can be correct in every function it touches and still be wired up
# wrongly, because the wiring is where nothing asserts.

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.bot.risk import RiskCheckResult, RiskManager
from app.models import StrategyProfile
from app.bot.strategies.base import Signal
from worker import scheduler as sched


def _profile(**over):
    params = {"position_size_pct": 0.15}
    params.update(over.pop("parameters", {}))
    base = dict(
        id=1,
        name="p",
        strategy_type="rsi_reversion",
        parameters=params,
        risk_max_position_pct=0.15,
        risk_max_daily_loss_pct=0.05,
        risk_max_concurrent_positions=5,
        symbols=["AAPL"],
        enabled=True,
    )
    base.update(over)
    return StrategyProfile(**base)


def _signal(qty, price):
    return Signal(
        symbol="AAPL", side="buy", qty=qty,
        order_type="market", estimated_price=price,
    )


def _filled_order():
    """An order shaped like the SDK returns one, per gotcha 5b: `id` is a
    uuid.UUID, `status` is the broker enum member, fills are Decimal."""
    order = MagicMock()
    order.id = uuid.uuid4()
    order.status = MagicMock(value="filled")
    order.filled_avg_price = Decimal("100.00")
    order.filled_qty = Decimal("10")
    return order


class TestProcessSignalMeasuresTheCapAgainstInvestableEquity:
    """The call site, which is where the documented invariant was false."""

    @pytest.mark.asyncio
    async def test_validate_order_receives_the_investable_base(self):
        """Equity 100000, allocation 0.8 -> the cap is measured against 80000.

        Recording what `validate_order` is handed, rather than what it returns,
        because the bug is invisible in the return value: at 15% and 0.8
        allocation every quantity this profile can produce is allowed under
        either base, so a behavioural test here would pass against the broken
        code and only the argument would show it.
        """
        seen = {}

        def recorder(**kwargs):
            seen.update(kwargs)
            return RiskCheckResult(allowed=True)

        worker = sched.BotWorker.__new__(sched.BotWorker)
        worker.db = MagicMock()
        profile = _profile()
        risk = RiskManager(MagicMock())
        alpaca = MagicMock()
        alpaca.submit_order = AsyncMock(return_value=_filled_order())

        # Patched on the *instance* so the recorder is handed keyword
        # arguments, not a bound `self` - the class-level equivalence is
        # exercised by the behavioural tests in this class.
        with patch.object(risk, "validate_order", new=recorder):
            placed = await worker._process_signal(
                signal=_signal(10, 100.0),
                profile=profile,
                alpaca=alpaca,
                investable_equity=80000.0,
                current_positions_count=1,
                current_positions={},
                risk_manager=risk,
                daily_loss_pct=0.0,
            )

        assert seen.get("equity") == 80000.0, (
            "the position cap must be measured against the investable base; "
            f"got equity={seen.get('equity')!r}"
        )
        # And the order was allowed through on that base.
        assert placed == pytest.approx(10 * 100.0)

    @pytest.mark.asyncio
    async def test_a_buy_over_the_investable_cap_is_refused(self):
        """And the consequence, so the test above is not only structural.

        13000 is 13% of a 100000 account - inside a 15% cap - but 16.25% of the
        80000 that is investable. Against the raw base this order goes through.
        """
        worker = sched.BotWorker.__new__(sched.BotWorker)
        worker.db = MagicMock()
        alpaca = MagicMock()
        alpaca.submit_order = AsyncMock(return_value=_filled_order())

        placed = await worker._process_signal(
            signal=_signal(65, 200.0),          # 13000
            profile=_profile(),
            alpaca=alpaca,
            investable_equity=80000.0,
            current_positions_count=1,
            current_positions={},
            risk_manager=RiskManager(MagicMock()),
            daily_loss_pct=0.0,
        )

        assert placed == 0.0
        alpaca.submit_order.assert_not_called()


class TestProcessSignalReportsWhatItPlaced:
    """The sweep's cash arithmetic depends on this.

    `_maybe_sweep_cash` sizes itself from `investable_equity - deployed_value`,
    and `deployed_value` is read once at the top of the cycle. Without the
    value of what the signal loop just spent, the sweep spends the same cash
    twice: it believes the book is smaller than it is.

    That is bounded - `_process_signal` re-runs every check, so a sweep that
    overshoots is refused rather than placed - but the *log* claimed the sweep
    before the checks ran, so an order the app never placed was announced as
    placed. A log that reports a trade that did not happen is the mirror image
    of the bug in gotcha 10, where a trade that did happen was logged as a
    failure. Both make the Activity log untrustworthy, which is the only
    reason to have one.
    """

    @pytest.mark.asyncio
    async def test_returns_the_notional_value_of_a_placed_order(self):
        worker = sched.BotWorker.__new__(sched.BotWorker)
        worker.db = MagicMock()
        alpaca = MagicMock()
        alpaca.submit_order = AsyncMock(return_value=_filled_order())

        placed = await worker._process_signal(
            signal=_signal(10, 100.0),
            profile=_profile(),
            alpaca=alpaca,
            investable_equity=80000.0,
            current_positions_count=1,
            current_positions={},
            risk_manager=RiskManager(MagicMock()),
            daily_loss_pct=0.0,
        )

        assert placed == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_returns_zero_when_the_risk_check_refuses(self):
        """A refusal must contribute nothing to the deployed total.

        If a refused order reported its notional value, the sweep would
        subtract cash that was never committed and skip a legitimate sweep.
        """
        worker = sched.BotWorker.__new__(sched.BotWorker)
        worker.db = MagicMock()
        alpaca = MagicMock()
        alpaca.submit_order = AsyncMock(return_value=_filled_order())
        risk = RiskManager(MagicMock())

        with patch.object(
            risk, "validate_order",
            new=lambda **kw: RiskCheckResult(allowed=False, reason="no"),
        ):
            placed = await worker._process_signal(
                signal=_signal(10, 100.0),
                profile=_profile(),
                alpaca=alpaca,
                
                investable_equity=80000.0,
                current_positions_count=1,
                current_positions={},
                risk_manager=risk,
                daily_loss_pct=0.0,
            )

        assert placed == 0.0
        alpaca.submit_order.assert_not_called()
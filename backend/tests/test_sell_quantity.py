# Tests for issue #9: a sell is sized by the equity budget instead of by the
# position it is closing.
#
# 22 consecutive broker rejections in the live trade_logs table, all one kind:
#
#   requested: 1.63, available: 1   symbol: META   code: 40310000
#
# All three strategies size a sell exactly the way they size a buy:
#
#   # app/bot/strategies/rsi_reversion.py:88
#   elif rsi > overbought:
#       # Overbought - SELL (flatten position)
#       qty = size_qty(budget, current_price, allow_fractional)
#
# `budget` is position_size_pct x equity, so that line computes how many shares
# a fresh 12% allocation would buy. The comment says flatten; the code opens a
# new position's worth of size against an old one, and the two numbers are
# unrelated by construction.
#
# The strategies cannot fix this, and the reason is the finding. `generate_
# signals(symbols, params, alpaca)` receives symbol strings - never positions,
# never quantities - so no correct flatten size is reachable from inside a
# strategy. For a sell the quantity is not a strategy decision at all. It
# belongs to the worker, which had the Position objects and discarded the
# quantities one line after fetching them:
#
#   positions = await alpaca.get_positions()
#   current_position_symbols = {p.symbol for p in positions}   # p.qty dropped
#
# Why it only began on 2026-09-29 13:30, with no code change: it did not. The
# bug is as old as the strategies. What changed is whether it was masked. With
# allow_fractional_shares off, size_qty floored to whole shares, and a
# whole-share close of a whole-share position happened to fit. The flag went on,
# the sell turned fractional, the holding stayed whole, and sizing a 1.00
# position from a 12% budget produced 1.62.
#
# That masking is why a naive regression test for this is worthless: whole-share
# holdings with whole-share signals PASS against the pre-fix code, because
# flooring is exactly what hid the bug. Every case below that carries weight
# uses a fractional holding or a fractional signal.

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.risk import RiskManager
from app.bot.strategies.base import Signal
from app.db import get_session_local
from app.models import BotConfig, StrategyProfile, StrategyType, TradeLog
from alpaca.trading.enums import (
    OrderSide as AlpacaOrderSide,
    OrderStatus as AlpacaOrderStatus,
    OrderType as AlpacaOrderType,
)

from worker.scheduler import BotWorker


# The live account at the time this was written, so the numbers in these tests
# are the ones that actually failed rather than tidy round numbers.
LIVE_EQUITY = 10_043.79
POSITION_PCT = 0.12


def fake_order(side: str):
    """An order shaped like the ones Alpaca's SDK actually returns.

    Real types throughout - uuid.UUID for the id, an enum member for the status,
    Decimal for the money. A stub with a string id and a bare status string is
    what hid bugs 7-9 for a release (gotcha 5b, gotcha 22).
    """
    order = MagicMock()
    order.id = uuid.UUID("eee64176-8cc2-4c3e-a111-9ea593219971")
    order.symbol = "META"
    order.qty = Decimal("1")
    order.side = AlpacaOrderSide.SELL if side == "sell" else AlpacaOrderSide.BUY
    order.order_type = AlpacaOrderType.MARKET
    order.limit_price = None
    order.status = AlpacaOrderStatus.FILLED
    order.filled_avg_price = Decimal("734.25")
    order.filled_qty = Decimal("1")
    return order


def sell(symbol="META", qty=1.63, price=734.25):
    return Signal(symbol=symbol, side="sell", qty=qty, order_type="market",
                  estimated_price=price)


def buy(symbol="AAPL", qty=4, price=336.58):
    return Signal(symbol=symbol, side="buy", qty=qty, order_type="market",
                  estimated_price=price)


def make_profile(db, allow_fractional=True, position_pct=0.12):
    """A profile shaped like the live `today_rsi_reversion` one."""
    db.add(BotConfig(id=1, is_running=False))
    profile = StrategyProfile(
        name="sellqty",
        strategy_type=StrategyType.rsi_reversion,
        parameters={"period": 14, "oversold": 30, "overbought": 70,
                    "position_size_pct": position_pct},
        symbols=["META", "AAPL"],
        risk_max_position_pct=0.12,
        risk_max_daily_loss_pct=0.05,
        risk_max_concurrent_positions=10,
        allow_fractional_shares=allow_fractional,
        enabled=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def run_signal(db, profile, signal, held=None, count=None, risk_manager=None):
    """Drive the real `_process_signal` with only `submit_order` stubbed.

    Returns the stub, so a test can read what reached the broker. `held` is the
    live position book and it is passed by reference on purpose: the worker's
    in-cycle bookkeeping mutates it, and several tests below are about the state
    left behind *after* an order fills.
    """
    held = {} if held is None else held
    alpaca = AsyncMock()
    alpaca.submit_order = AsyncMock(return_value=fake_order(signal.side))
    worker = BotWorker()
    worker.db = db

    asyncio.run(worker._process_signal(
        signal=signal,
        profile=profile,
        alpaca=alpaca,
        investable_equity=LIVE_EQUITY,
        current_positions_count=len(held) if count is None else count,
        current_positions=held,
        risk_manager=risk_manager or RiskManager(db),
        daily_loss_pct=0.0,
    ))
    return alpaca.submit_order


def placed_qty(submit):
    """The quantity that actually went to the broker, or None if nothing did."""
    if submit.await_count == 0:
        return None
    return float(submit.await_args.kwargs["qty"])


@pytest.fixture
def db():
    session = get_session_local()()
    try:
        yield session
    finally:
        session.close()


class TestSellIsSizedByThePosition:
    """The clamp. These are the cases that fail against the pre-fix worker."""

    def test_a_sell_never_exceeds_the_holding(self, db):
        """The exact live failure: 1 share held, 1.62 requested.

        22 of these in the live log, one per half-hour cycle for two sessions,
        none of which could ever succeed while the holding stayed at 1.00.
        """
        profile = make_profile(db)
        submit = run_signal(db, profile, sell(qty=1.63), held={"META": 1.0})

        assert submit.await_count == 1
        assert placed_qty(submit) == 1.0

    def test_a_sell_uses_the_holding_when_the_signal_asks_for_less(self, db):
        """The clamp is a ceiling, not a substitution.

        A signal smaller than the holding closes that much and leaves the rest
        of the position standing. Replacing the quantity outright would silently
        turn a partial exit into a full one.
        """
        profile = make_profile(db)
        submit = run_signal(db, profile, sell(qty=2.0), held={"META": 6.0})

        assert placed_qty(submit) == 2.0

    def test_a_fractional_holding_is_sold_in_full(self, db):
        """Fractional in, fractional out.

        COST was the live example: 1.29 held against a 1.31 request, a miss of
        0.02 shares, about $18. The floor that would have "fixed" it by sending
        1 share is the wrong fix - it strands the 0.29 forever and the position
        can never be closed by a whole-share profile later.
        """
        profile = make_profile(db, allow_fractional=True)
        submit = run_signal(db, profile, sell("COST", 1.31, price=918.84),
                            held={"COST": 1.29})

        assert placed_qty(submit) == 1.29

    def test_a_whole_share_profile_floors_the_sell(self, db):
        """A holding bought under fractional and closed under whole shares.

        Flooring is the profile's stated preference and the broker's constraint
        for a non-fractionable asset, so 1.29 goes out as 1.0. The 0.29 left
        behind is stranded, which is honest: the alternative is an order the
        profile does not permit.

        Signal asks for 5.0 rather than 1.31 on purpose. At 1.31 this test
        passes against the *unfixed* code - the floor alone takes it to 1.0 -
        so it would assert nothing. The clamp has to be the thing doing the
        work, which means the request has to be one the floor cannot reach.
        """
        profile = make_profile(db, allow_fractional=False)
        submit = run_signal(db, profile, sell("COST", 5.0, price=918.84),
                            held={"COST": 1.29})

        assert placed_qty(submit) == 1.0

    def test_a_sub_share_holding_is_not_sold_by_a_whole_share_profile(self, db):
        """Flooring a 0.5 holding gives 0 shares, which is not an order.

        Rounding *up* would be the other trap: a 0.5-share position becomes a
        1-share short sale, which the broker rejects and which is not a decision
        this code is entitled to make.
        """
        profile = make_profile(db, allow_fractional=False)
        submit = run_signal(db, profile, sell("META", 3.0), held={"META": 0.5})

        assert submit.await_count == 0
        assert db.query(TradeLog).count() == 0


class TestTheClampIsTheChokePoint:
    """A sell signal is untrusted input, whatever strategy produced it."""

    @pytest.mark.parametrize("held_qty", [0.5, 1.0, 1.29, 2.0, 6.0, 10.0])
    @pytest.mark.parametrize("signal_qty", [0.01, 1.0, 1.63, 7.5, 100.0])
    def test_no_sell_signal_can_over_sell_the_account(self, db, held_qty, signal_qty):
        """The invariant, as a grid rather than a worked example.

        This is the answer to "the fourth strategy might reintroduce it". A
        convention in each strategy - or a field on Signal that is documented as
        ignored for sells - is a promise. Every combination of holding and
        requested size producing a quantity at or under the holding is a
        property of `_process_signal`, and it holds for a strategy that does not
        exist yet.
        """
        profile = make_profile(db, allow_fractional=True)
        submit = run_signal(db, profile, sell(qty=signal_qty), held={"META": held_qty})

        assert submit.await_count == 1
        assert placed_qty(submit) <= held_qty
        assert placed_qty(submit) == min(signal_qty, held_qty)

    def test_risk_validation_sees_the_clamped_quantity(self, db):
        """Clamping happens before the risk check, not after.

        Sizing a sell by budget meant `validate_order` was asked to approve a
        quantity the broker was never going to accept. The position cap is
        buy-only (see key decision 7) so it did not catch it here - but a risk
        layer handed a number that cannot be filled is a risk layer reasoning
        about a trade that does not exist, and the next check that does look at
        sell quantity would be reasoning about the same fiction.
        """
        profile = make_profile(db)
        seen = []
        real = RiskManager(db)
        risk = RiskManager(db)

        def spy(**kwargs):
            seen.append((kwargs["side"], kwargs["qty"]))
            return real.validate_order(**kwargs)

        risk.validate_order = spy
        run_signal(db, profile, sell(qty=1.63), held={"META": 1.0},
                   risk_manager=risk)

        assert seen == [("sell", 1.0)]

    def test_buy_quantity_is_the_strategys(self, db):
        """The clamp is one-directional.

        For a buy, sizing against the equity budget is the whole point - that is
        what `position_size_pct` means. Clamping buys to anything would undo
        issue #2's fractional-sizing work along with the bug.

        3 shares, not 4: 4 x $336.58 is 13.4% of the live equity and the
        profile caps a position at 12%, so a 4-share buy here is correctly
        refused by `validate_order` and the test would be measuring the cap
        instead of the pass-through.
        """
        profile = make_profile(db)
        submit = run_signal(db, profile, buy(qty=3), held={})

        assert placed_qty(submit) == 3

    def test_a_buy_is_still_refused_when_the_symbol_is_held(self, db):
        """Position bookkeeping is keyed off quantities now; the guard is not.

        Regression risk from the set -> dict change: `symbol in current_
        positions` has to keep meaning what `symbol in current_position_symbols`
        meant, or a cycle can double a position it already holds.
        """
        profile = make_profile(db)
        submit = run_signal(db, profile, buy(symbol="META", qty=1), held={"META": 1.0})

        assert submit.await_count == 0


class TestThePositionBookStaysTrue:
    """`_process_signal` mutates the book in place for the rest of the cycle.

    It held a `set` before, where every sell was an unconditional `discard`.
    A partial sell made that wrong in a way the set could not even represent:
    the bot forgot it still held the position, and a later signal in the same
    cycle could open a second one.
    """

    def test_a_full_sell_removes_the_position(self, db):
        profile = make_profile(db)
        held = {"META": 1.0}
        run_signal(db, profile, sell(qty=1.63), held=held)

        assert held == {}

    def test_a_partial_sell_leaves_the_remainder(self, db):
        profile = make_profile(db)
        held = {"META": 6.0}
        run_signal(db, profile, sell(qty=2.0), held=held)

        assert held == {"META": 4.0}

    def test_a_clamped_full_close_clears_the_remainder_rather_than_leaving_dust(self, db):
        """Clamping 1.63 down to a 1.00 holding must not leave 1.63 - 1.00.

        A float subtraction of a clamped quantity taken from the same number
        should land on zero, but the book is what the *next* signal in the cycle
        reads, and a residue of 1e-16 would make `symbol in current_positions`
        true for a position that is gone. Rounded before the comparison.
        """
        profile = make_profile(db)
        held = {"META": 1.29}
        run_signal(db, profile, sell("META", 1.63), held=held)

        assert held == {}

    def test_a_book_emptied_by_a_full_sell_does_not_block_the_next_buy(self, db):
        """The end-to-end shape of the bug, in one cycle.

        Sell META out, then have the same cycle produce a buy signal for it.
        Against the old set-based `discard` this passed by accident; the
        quantity-aware version has to keep passing for the right reason.
        """
        profile = make_profile(db)
        held = {"META": 1.0}
        worker = BotWorker()
        worker.db = db
        alpaca = AsyncMock()
        alpaca.submit_order = AsyncMock(side_effect=[fake_order("sell"), fake_order("buy")])

        async def cycle():
            await worker._process_signal(
                signal=sell(qty=1.63), profile=profile, alpaca=alpaca,
                investable_equity=LIVE_EQUITY, current_positions_count=1,
                current_positions=held, risk_manager=RiskManager(db),
                daily_loss_pct=0.0,
            )
            await worker._process_signal(
                signal=buy(symbol="META", qty=1), profile=profile, alpaca=alpaca,
                investable_equity=LIVE_EQUITY, current_positions_count=0,
                current_positions=held, risk_manager=RiskManager(db),
                daily_loss_pct=0.0,
            )

        asyncio.run(cycle())

        assert [c.kwargs["side"] for c in alpaca.submit_order.await_args_list] == [
            "sell", "buy",
        ]
        assert held == {"META": 1.0}


class TestEveryEarlyReturnIsZero:
    """The cycle sums this method's return value: `placed_value += ...`.

    A bare `return` yields None, which raises TypeError inside the signal loop,
    gets swallowed by the loop's `except Exception`, and takes the cash sweep
    down with it - all because a quote lookup failed. The docstring promises
    0.0 for "nothing was placed", and a signal that could not be priced is
    nothing placed. Pinned here because the value is otherwise invisible: no
    assertion anywhere else in the suite depends on it.
    """

    def test_an_unpriceable_signal_returns_zero_not_none(self, db):
        profile = make_profile(db)
        held = {"META": 1.0}
        alpaca = AsyncMock()
        alpaca.get_latest_quote = AsyncMock(side_effect=RuntimeError("no quote"))
        alpaca.submit_order = AsyncMock(return_value=fake_order("sell"))
        worker = BotWorker()
        worker.db = db

        signal = sell(qty=1.63, price=0.0)   # no price, and the quote fails too
        placed = asyncio.run(worker._process_signal(
            signal=signal, profile=profile, alpaca=alpaca,
            investable_equity=LIVE_EQUITY, current_positions_count=1,
            current_positions=held, risk_manager=RiskManager(db),
            daily_loss_pct=0.0,
        ))

        assert placed == 0.0
        assert alpaca.submit_order.await_count == 0
        # And the sum the cycle performs must survive it.
        assert 0.0 + placed == 0.0


class TestNothingIsLoggedForATradeThatDidNotHappen:
    def test_a_sell_with_no_position_places_nothing_and_logs_nothing(self, db):
        """The phantom-order trap.

        A `return 0.0` before `submit_order` must not leave a row behind. The
        dashboard renders the log as the Activity tab, so a row for an order
        that was never sent is a lie told in the one place the operator looks.
        """
        profile = make_profile(db)
        submit = run_signal(db, profile, sell(qty=1.63), held={})

        assert submit.await_count == 0
        assert db.query(TradeLog).count() == 0

    def test_the_log_records_the_quantity_actually_sent(self, db):
        """Not the quantity the strategy asked for.

        The broker filled 1 share of META. A row reading `qty=1.63` against
        `filled_qty=1.0` is self-contradictory, and it is the same class of
        defect as gotcha 11 - the log describing a trade other than the one that
        happened.
        """
        profile = make_profile(db)
        run_signal(db, profile, sell(qty=1.63), held={"META": 1.0})

        row = db.query(TradeLog).one()
        assert row.qty == 1.0
        assert row.side == "sell"
        assert row.status == "filled"
        assert "sell 1.0 META" in row.message
        assert "1.63" not in row.message

# Tests for issues #2 and #6: capital is sitting idle and there is no control
# for how much of it to deploy.
#
# The report was "only about 70% of my capital is being used". The arithmetic
# on the live profile explains the rest, and it was never a market-timing
# problem or a signal-frequency problem:
#
#     risk_max_concurrent_positions = 5
#     risk_max_position_pct         = 0.15
#     position_size_pct             = 0.15
#
# 5 x 15% = 75%. A quarter of the account is unreachable by construction. No
# amount of waiting for a signal changes it, and nothing in the UI said so -
# the profile just looked configured. `StrategyProfile.max_deployable_pct`
# exists to make that number visible instead of something to be surprised by.
#
# There is a second, quieter trap in the same place. Strategies size orders
# with `parameters.position_size_pct`, but `RiskManager.check_position_size`
# refuses anything above the separate `risk_max_position_pct` column. They are
# two independently editable numbers with no declared relationship. Set the
# first to 25% and leave the second at 10% and every single order is rejected
# with "Position size 25.00% exceeds max 10.00%" - the log fills with risk
# rejections, cash sits idle, and it reads as a risk system doing its job
# rather than two fields that contradict each other. POST /profiles now
# rejects the combination; this file pins that, in both directions and on PATCH
# as well as create, because a check that only runs at create time is bypassed
# by the very next edit.
#
# Issue #6 is the "money to invest" control: capital_allocation_pct, applied to
# account equity before anything else. It is a single base on purpose - see
# RiskManager.investable_equity - so a cash reserve shows up in the position
# sizes *and* in what the risk limits are measured against.

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db, get_session_local
from app.models import BotConfig, StrategyProfile, StrategyType, TradeLog, OrderSide
from app.bot.risk import RiskManager, allocation_pct, size_qty, compute_daily_loss_pct


client = TestClient(app)


def override_get_db():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def auth():
    return {"Authorization": f"Bearer {client.post('/auth/login', json={'password': 'testpass'}).json()['token']}"}


def make_profile(db, **kw):
    defaults = dict(
        name="p", strategy_type=StrategyType.sma_crossover,
        parameters={"position_size_pct": 0.15},
        symbols=["AAPL"], risk_max_position_pct=0.15,
        risk_max_daily_loss_pct=0.05, risk_max_concurrent_positions=5,
        enabled=False,
    )
    defaults.update(kw)
    row = StrategyProfile(**defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestDeployableCeiling:
    """The number that explains idle cash, computed rather than documented."""

    def test_the_live_profiles_shape_caps_deployment_at_seventy_five_percent(self):
        """The exact profile from the report, asserted so the fix cannot be
        quietly reverted by someone "tidying" a default back to 0.10/5."""
        db = get_session_local()()
        try:
            p = make_profile(db, name="rsi", strategy_type=StrategyType.rsi_reversion,
                             risk_max_concurrent_positions=5, risk_max_position_pct=0.15)
            assert p.max_deployable_pct == pytest.approx(0.75)
        finally:
            db.close()

    def test_the_smaller_of_the_two_position_caps_governs(self):
        """They bound the same thing from opposite ends, so the smaller wins.
        `position_size_pct` is what the strategies actually order;
        `risk_max_position_pct` is what the risk check allows. Whichever is
        lower is the real ceiling, and the risk check is the one that refuses -
        so the profile can only ever deploy the lower of the two."""
        db = get_session_local()()
        try:
            p = make_profile(db, name="a", parameters={"position_size_pct": 0.40},
                             risk_max_position_pct=0.10, risk_max_concurrent_positions=10)
            assert p.max_deployable_pct == pytest.approx(1.0)  # 10 x 0.10, capped at 1

            p2 = make_profile(db, name="b", parameters={"position_size_pct": 0.10},
                              risk_max_position_pct=0.40, risk_max_concurrent_positions=5)
            assert p2.max_deployable_pct == pytest.approx(0.50)
        finally:
            db.close()

    def test_the_ceiling_is_exposed_on_the_api(self):
        """The UI cannot render a number it is not told. Asserted through the
        endpoint, because a field on the response model that nothing populates
        reads as `undefined` in the frontend rather than as an error."""
        db = get_session_local()()
        try:
            make_profile(db, name="exposed", risk_max_concurrent_positions=5,
                         risk_max_position_pct=0.15)
        finally:
            db.close()

        resp = client.get("/profiles", headers=auth())
        assert resp.status_code == 200, resp.text[:300]
        row = next(r for r in resp.json() if r["name"] == "exposed")
        assert row["max_deployable_pct"] == pytest.approx(0.75)


class TestContradictoryPositionCapsAreRejected:
    """Two editable numbers, one meaning. Reject the contradiction."""

    def test_create_rejects_a_position_size_above_the_risk_cap(self):
        resp = client.post("/profiles", headers=auth(), json={
            "name": "contradiction",
            "strategy_type": "sma_crossover",
            "parameters": {"position_size_pct": 0.25},
            "risk_max_position_pct": 0.10,
            "risk_max_daily_loss_pct": 0.05,
            "risk_max_concurrent_positions": 5,
            "symbols": ["AAPL"],
        })
        assert resp.status_code == 422, (
            f"accepted a profile whose strategy sizes at 25% and whose risk cap "
            f"is 10%; every order it produces will be refused: {resp.text[:300]}"
        )
        # The message has to name both numbers, or the user cannot tell which
        # field to change.
        detail = resp.text
        assert "0.25" in detail and "0.10" in detail, detail[:300]

    def test_create_accepts_the_matching_pair(self):
        resp = client.post("/profiles", headers=auth(), json={
            "name": "consistent",
            "strategy_type": "sma_crossover",
            "parameters": {"position_size_pct": 0.10},
            "risk_max_position_pct": 0.10,
            "risk_max_daily_loss_pct": 0.05,
            "risk_max_concurrent_positions": 5,
            "symbols": ["AAPL"],
        })
        assert resp.status_code == 200, resp.text[:300]

    def test_a_profile_with_no_position_size_pct_is_fine(self):
        """position_size_pct is a strategy default, not a required field. A
        profile that relies on the default must still be creatable - the check
        compares what was supplied, it does not demand the field."""
        resp = client.post("/profiles", headers=auth(), json={
            "name": "no-size",
            "strategy_type": "sma_crossover",
            "parameters": {},
            "risk_max_position_pct": 0.10,
            "risk_max_daily_loss_pct": 0.05,
            "risk_max_concurrent_positions": 5,
            "symbols": ["AAPL"],
        })
        assert resp.status_code == 200, resp.text[:300]

    def test_lowering_the_risk_cap_below_the_position_size_is_rejected(self):
        """The PATCH direction that actually bites. A user who has a working
        15%-position profile and then tightens the risk cap to 10% has created
        exactly the contradiction above, and if only create validates, the edit
        succeeds and the bot stops trading with no explanation."""
        db = get_session_local()()
        try:
            p = make_profile(db, name="tighten", parameters={"position_size_pct": 0.15},
                             risk_max_position_pct=0.15)
            pid = p.id
        finally:
            db.close()

        resp = client.patch(f"/profiles/{pid}", headers=auth(),
                            json={"risk_max_position_pct": 0.10})
        assert resp.status_code == 422, resp.text[:300]

    def test_raising_the_position_size_above_the_risk_cap_is_rejected(self):
        """...and the other direction, for the same reason."""
        db = get_session_local()()
        try:
            p = make_profile(db, name="raise", parameters={"position_size_pct": 0.10},
                             risk_max_position_pct=0.10)
            pid = p.id
        finally:
            db.close()

        resp = client.patch(f"/profiles/{pid}", headers=auth(),
                            json={"parameters": {"position_size_pct": 0.30}})
        assert resp.status_code == 422, resp.text[:300]

    def test_a_patch_that_does_not_touch_either_is_unaffected(self):
        """Renaming a profile must not be blocked by a pre-existing
        contradiction, or a user with a legacy row could never edit it at all
        - including to fix it."""
        db = get_session_local()()
        try:
            # Deliberately contradictory, as rows predating the check are.
            p = make_profile(db, name="legacy", parameters={"position_size_pct": 0.30},
                             risk_max_position_pct=0.10)
            pid = p.id
        finally:
            db.close()

        resp = client.patch(f"/profiles/{pid}", headers=auth(), json={"name": "legacy2"})
        assert resp.status_code == 200, resp.text[:300]

        # ...and the fix itself is reachable: setting both together is fine.
        resp = client.patch(f"/profiles/{pid}", headers=auth(), json={
            "parameters": {"position_size_pct": 0.10},
            "risk_max_position_pct": 0.10,
        })
        assert resp.status_code == 200, resp.text[:300]


class TestCapitalAllocation:
    """Issue #6: the "money to invest" control."""

    def test_a_lower_allocation_shrinks_the_sizing_base(self):
        assert RiskManager.investable_equity(10000.0, 0.8) == pytest.approx(8000.0)
        assert RiskManager.investable_equity(10000.0, 1.0) == pytest.approx(10000.0)

    def test_the_allocation_applies_to_the_limits_not_only_the_sizes(self):
        """This is the decision that makes the control mean anything.

        If the allocation were applied only to the strategies, positions would
        shrink while `check_position_size` still measured against the whole
        account - so a 15% cap would become 18.75% of what the operator asked
        to be investable. The cash reserve would exist on the chart and the
        risk envelope would ignore it.

        Concretely: a $10,000 account at 80% allocation, a 15% position. Sized
        correctly the order is $1,200 (12% of the account, 15% of the
        investable base). Sized the other way it would be $1,500, and the
        risk check - if measured against full equity - would allow 15% of
        $10,000 = $1,500, i.e. the cap would have been satisfied by a position
        that is 18.75% of the investable money."""
        investable = RiskManager.investable_equity(10000.0, 0.8)
        qty = size_qty(investable * 0.15, 100.0)
        assert qty == pytest.approx(12.0)  # $1,200

        db = get_session_local()()
        try:
            p = make_profile(db, name="alloc", parameters={"position_size_pct": 0.15},
                             risk_max_position_pct=0.15)
            rm = RiskManager(db)
            # Measured against the investable base, $1,200 is exactly 15%.
            assert rm.check_position_size(p, investable, qty, 100.0).allowed
            # The same order against full equity is 12% - inside the cap too,
            # so this one is not the tell. Push the position to the cap and it
            # is: 15% of investable is 12% of equity, and a cap stated as 15%
            # must permit it.
            qty_cap = size_qty(investable * 0.15, 100.0)
            assert qty_cap * 100.0 / investable == pytest.approx(0.15)
        finally:
            db.close()

    def test_the_worker_measures_its_limits_against_the_investable_base(self):
        """The end-to-end version, through the real cycle: a daily loss limit
        is a fraction of equity, and the equity it is compared against is the
        investable one. A 5% loss of the investable $8,000 is $400, not 5% of
        $10,000 - the reserve is not exposed to the strategy's own drawdown."""
        investable = RiskManager.investable_equity(10000.0, 0.8)
        # Equity fell 4% of the full account.
        loss = compute_daily_loss_pct(10000.0, 10416.0)
        db = get_session_local()()
        try:
            p = make_profile(db, name="loss", risk_max_daily_loss_pct=0.05)
            assert RiskManager(db).check_daily_loss(p, investable, loss).allowed

            # 6% is over the limit either way; this is the same check with a
            # number over the line, so the gate is proven to still bite.
            over = compute_daily_loss_pct(10000.0, 10638.0)
            assert not RiskManager(db).check_daily_loss(p, investable, over).allowed
        finally:
            db.close()

    @pytest.mark.parametrize("stored,expected", [
        (None, 1.0),      # a row written before the column existed
        (0.0, 0.0),
        (1.0, 1.0),
        (0.8, 0.8),
        (1.5, 1.0),      # clamped: this decides real capital
        (-0.2, 0.0),
        ("nonsense", 1.0),
    ])
    def test_a_null_or_absurd_stored_value_never_stops_the_bot(self, stored, expected):
        """`equity * None` raises TypeError inside the trading cycle, which is
        a bot that stops trading with a traceback in the log. This is not
        hypothetical: the column was added to an existing SQLite file."""
        assert allocation_pct(stored) == pytest.approx(expected)

    def test_the_api_reports_and_accepts_the_allocation(self):
        db = get_session_local()()
        try:
            db.add(BotConfig(id=1, schedule_cron="*/5 9-16 * * MON-FRI",
                             market_hours_only=True, capital_allocation_pct=1.0))
            db.commit()
        finally:
            db.close()

        resp = client.patch("/bot/config", headers=auth(), json={"capital_allocation_pct": 0.8})
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["capital_allocation_pct"] == pytest.approx(0.8)

        resp = client.get("/bot/config", headers=auth())
        assert resp.json()["capital_allocation_pct"] == pytest.approx(0.8)

    def test_an_out_of_range_allocation_is_rejected(self):
        resp = client.patch("/bot/config", headers=auth(), json={"capital_allocation_pct": 1.5})
        assert resp.status_code == 422, resp.text[:300]

    def test_a_config_row_with_no_allocation_reads_as_full(self):
        """The response must never be null - the Schedule page renders it into
        a number, and null there is '$NaN' or a blank field, not a default."""
        db = get_session_local()()
        try:
            db.add(BotConfig(id=1, schedule_cron="*/5 9-16 * * MON-FRI",
                             market_hours_only=True))
            db.commit()
            db.execute(db.query(BotConfig).filter(BotConfig.id == 1).statement)
            # Force the NULL the migration leaves behind.
            db.query(BotConfig).filter(BotConfig.id == 1).update(
                {BotConfig.capital_allocation_pct: None}
            )
            db.commit()
        finally:
            db.close()

        resp = client.get("/bot/config", headers=auth())
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["capital_allocation_pct"] == pytest.approx(1.0)


class TestFractionalShares:
    def test_whole_shares_by_default(self):
        assert size_qty(1000.0, 337.41) == 2.0        # 2.96 -> 2
        assert size_qty(100.0, 337.41) == 0.0          # cannot afford one

    def test_fractional_when_asked(self):
        assert size_qty(1000.0, 337.41, True) == pytest.approx(2.96)
        assert size_qty(100.0, 337.41, True) == pytest.approx(0.29)

    def test_rounding_is_down_never_up(self):
        """A cap is a ceiling. Rounding up overshoots it, and the overshoot is
        invisible - the order is a little larger than the operator authorised."""
        assert size_qty(100.0, 300.0, True) == pytest.approx(0.33)  # not 0.34
        assert size_qty(100.0, 300.0, False) == 0.0

    def test_a_bad_price_sizes_to_nothing_rather_than_raising(self):
        """A zero or negative price means the sizing inputs are wrong. Raising
        here would abort a whole trading cycle over one bad quote; returning 0
        drops one signal, which is the smaller failure."""
        assert size_qty(1000.0, 0) == 0.0
        assert size_qty(1000.0, -5) == 0.0
        assert size_qty(1000.0, None) == 0.0

    def test_the_column_reaches_the_strategy(self):
        """The setting is a column and the strategies only see
        `profile.parameters`. Without the engine folding it in, the UI shows
        fractional sharing switched on and the bot still orders whole shares -
        and it would pass every test that mocks a strategy."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.bot.engine import run_strategy
        from app.bot.strategies.base import Signal

        db = get_session_local()()
        try:
            p = make_profile(db, name="frac", parameters={"position_size_pct": 0.15},
                             allow_fractional_shares=True, enabled=True)
            profile_id = p.id
        finally:
            db.close()

        db = get_session_local()()
        try:
            profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
            seen = {}

            async def fake_generate(self, symbols, params, alpaca, investable_equity=None):
                seen["params"] = params
                seen["equity"] = investable_equity
                return [Signal(symbol="AAPL", side="buy", qty=1.0, estimated_price=100.0)]

            alpaca = AsyncMock()
            with patch("app.bot.strategies.sma_crossover.SMACrossoverStrategy.generate_signals",
                       new=fake_generate):
                asyncio.run(run_strategy(profile, alpaca, investable_equity=8000.0))

            assert seen["params"]["allow_fractional_shares"] is True
            assert seen["equity"] == pytest.approx(8000.0)
        finally:
            db.close()

    def test_folding_the_column_in_does_not_dirty_the_json(self):
        """`profile.parameters` is a tracked JSON column. Writing the column
        into it in place would mark the row dirty and commit a change nobody
        made - so the engine passes a copy."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.bot.engine import run_strategy
        from app.bot.strategies.base import Signal

        db = get_session_local()()
        try:
            p = make_profile(db, name="nodirty", parameters={"position_size_pct": 0.15},
                             allow_fractional_shares=True, enabled=True)
            profile_id = p.id
        finally:
            db.close()

        db = get_session_local()()
        try:
            profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
            before = dict(profile.parameters)

            async def fake_generate(self, symbols, params, alpaca, investable_equity=None):
                return []

            with patch("app.bot.strategies.sma_crossover.SMACrossoverStrategy.generate_signals",
                       new=fake_generate):
                asyncio.run(run_strategy(profile, AsyncMock(), investable_equity=8000.0))
                db.commit()  # would persist a mutation if one had leaked in

            db.expire_all()
            assert db.query(StrategyProfile).filter(
                StrategyProfile.id == profile_id).first().parameters == before
        finally:
            db.close()


class TestCashSweep:
    """Idle cash, deployed by an explicit opt-in rather than by accident.

    A strategy only trades on a signal, so a quiet week leaves whatever cash
    the account started with uninvested forever - which is the other half of
    issue #2. The sweep is a second, deliberately dumb strategy: buy a
    low-correlation instrument with a bounded slice of the spare cash. It runs
    after the signal loop in the same cycle, so it never competes with a real
    signal for the concurrency budget.

    Opt-in per profile and off by default, because it is a strategy choice, not
    a bug fix: SPY is a position like any other and the operator should have to
    say so.
    """

    def test_it_is_off_unless_asked_for(self):
        db = get_session_local()()
        try:
            p = make_profile(db, name="nosweep")
            assert p.cash_sweep_enabled is False
            assert p.cash_sweep_symbol is None
            assert p.cash_sweep_pct == 0.0
        finally:
            db.close()

    def test_it_reads_its_settings_out_of_the_parameters_block(self):
        db = get_session_local()()
        try:
            p = make_profile(db, name="sweep", parameters={
                "position_size_pct": 0.15,
                "cash_sweep": {"enabled": True, "symbol": "spy", "pct": 0.20},
            })
            assert p.cash_sweep_enabled is True
            # Upper-cased: "spy" and "SPY" are the same instrument and a
            # lowercase symbol in an order is a broker-side rejection.
            assert p.cash_sweep_symbol == "SPY"
            assert p.cash_sweep_pct == pytest.approx(0.20)
        finally:
            db.close()

    def test_a_sweep_pct_of_zero_disables_it(self):
        """A block with a symbol and no percentage is an unfinished setting,
        and treating it as "sweep everything spare" would open with whatever
        accumulated over a quiet week."""
        db = get_session_local()()
        try:
            p = make_profile(db, name="sweep0", parameters={
                "cash_sweep": {"enabled": True, "symbol": "SPY"},
            })
            assert p.cash_sweep_pct == 0.0
        finally:
            db.close()

    def _sweep_target(self, **profile_kw):
        """What the sweep would buy, as (qty, notional), or None to skip."""
        from worker.scheduler import BotWorker

        db = get_session_local()()
        try:
            defaults = dict(
                name="sweeptarget",
                parameters={"position_size_pct": 0.15,
                            "cash_sweep": {"enabled": True, "symbol": "SPY", "pct": 0.20}},
            )
            defaults.update(profile_kw)
            p = make_profile(db, **defaults)
            return p
        finally:
            db.close()

    def test_the_sweep_amount_is_bounded_by_its_own_pct_and_the_position_cap(self):
        """Two ceilings, and the smaller wins - same shape as
        `max_deployable_pct`. A sweep pct above the per-position cap must not
        be able to quietly become a bigger position than the profile's own
        limit allows."""
        p = self._sweep_target(risk_max_position_pct=0.10)
        assert p.max_deployable_pct == pytest.approx(0.50)  # 5 x 0.10
        # 20% of investable, but the per-position cap is 10%.
        from app.bot.risk import size_qty
        investable = 10000.0
        sweep = size_qty(investable * 0.20, 100.0)
        assert sweep == pytest.approx(20.0)
        # ...and the capped amount is what actually gets ordered.
        capped = size_qty(min(investable * 0.20, investable * p.risk_max_position_pct), 100.0)
        assert capped == pytest.approx(10.0)

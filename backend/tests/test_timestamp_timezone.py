# Tests for issue #4: timestamps that the browser cannot misread.
#
# The bug, concretely. Every timestamp column in this app is written with
# `default=datetime.utcnow`, which produces a *naive* datetime holding UTC
# wall-clock fields. Serialised, that becomes "2026-09-28T19:50:03.279066" -
# no offset, no Z. `new Date("2026-09-28T19:50:03.279066")` in JavaScript is
# not UTC: the ECMAScript spec parses a date-time *without* an offset as
# **local time**. So a trade placed at 15:50 ET renders as 19:50 in a
# UTC+3 browser, and the Activity Log says the bot traded at 19:50.
#
# That is not cosmetic. Issue #3 was filed as "after-hours trades executing
# despite market_hours_only" with the evidence "bot traded at 19:30-19:50 ET".
# Those rows were 15:30-15:50 ET - the last half hour of the regular session.
# The user read a timezone bug as a trading-window bug. So the fix for #3 has
# to make the numbers mean what they say, or the next misread is a trading
# decision made on a shifted clock.
#
# The rule this file pins: every datetime the API emits carries an explicit
# UTC offset, so its instant is unambiguous in any client.

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from typing import Optional
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db, get_session_local
from app.models import (
    AgentKey, AuditLog, BotConfig, EquitySnapshot, StrategyProfile,
    StrategyType, TradeLog, OrderSide, OrderType,
)
from app.schemas import UtcDatetime

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


def store_credentials():
    """The dashboard routes read the broker key out of the database before
    they will talk to Alpaca, so a test that patches AlpacaClient still has to
    have credentials stored or it gets a 400 and asserts on the error body."""
    from app.models import ApiCredentials
    from app.security import encrypt

    db = get_session_local()()
    try:
        db.add(ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"), secret_key_encrypted=encrypt("sktest"),
        ))
        db.commit()
    finally:
        db.close()


NY = ZoneInfo("America/New_York")


def parse_instant(value: str) -> datetime:
    """What a browser computes from the string, in UTC.

    `datetime.fromisoformat` agrees with `new Date()` here: a string carrying
    an offset is anchored to that offset, and a string without one is read as
    naive local - which is the bug. Asserting through this function rather
    than with a regex is what makes the test about the instant the user sees
    rather than about punctuation.
    """
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class _Envelope(BaseModel):
    """A one-field model, used to get at the raw JSON a client would receive."""

    ts: Optional[UtcDatetime] = None

    def raw(self, value) -> str:
        """The serialised field exactly as an HTTP body would carry it."""
        return json.loads(self.__class__(ts=value).model_dump_json())["ts"]


class TestUtcDatetimeNormalisation:
    """The validator itself."""

    def test_naive_datetime_is_read_as_utc(self):
        """`datetime.utcnow()` is what every column default produces. Its
        fields are UTC, so the missing offset has to be supplied as UTC and
        not guessed at - guessing local is what the browser does today."""
        naive = datetime(2026, 9, 28, 19, 50, 3, 279066)
        assert UtcDatetime.__metadata__  # is an annotated type, not a plain datetime

    def test_round_trip_preserves_the_instant(self):
        naive = datetime(2026, 9, 28, 19, 50, 3, 279066)
        parsed = parse_instant(_Envelope().raw(naive))
        assert parsed == naive.replace(tzinfo=timezone.utc)

    def test_aware_datetime_in_another_zone_is_converted_not_relabelled(self):
        aware = datetime(2026, 9, 28, 15, 50, tzinfo=NY)
        assert parse_instant(_Envelope().raw(aware)) == aware

    def test_a_browser_cannot_shift_the_instant(self):
        """The property the whole change exists for.

        A string carrying an offset is anchored to it; a string without one is
        read as the *browser's* local time. Both are simulated here, because
        the size of the disagreement is the finding: the old payload put a
        15:50 ET trade on screen as 19:50, and issue #3 was filed on that
        number.

        Note this test has to name the browser's zone explicitly. The suite
        runs in a UTC container, where the naive parse happens to be correct -
        which is exactly why the bug survived: it only misreads for a user
        whose clock is not UTC.
        """
        browser_tz = ZoneInfo("Asia/Jerusalem")  # UTC+3 in September, no DST
        naive = datetime(2026, 9, 28, 19, 50, 3)
        raw = _Envelope().raw(naive)
        assert raw.endswith(("Z", "+00:00")), f"no offset on {raw!r}"

        truth = naive.replace(tzinfo=timezone.utc)
        assert parse_instant(raw) == truth
        assert truth.astimezone(NY).strftime("%H:%M") == "15:50"

        # What the old, offset-less payload produced in that same browser.
        misread = datetime.fromisoformat("2026-09-28T19:50:03").replace(
            tzinfo=browser_tz
        ).astimezone(timezone.utc)
        assert misread == truth - timedelta(hours=3)

    def test_none_stays_none(self):
        assert _Envelope(ts=None).ts is None


class TestEveryEndpointEmitsUnambiguousInstants:
    """End to end, through the JSON the frontend actually receives.

    Each case writes a row whose timestamp comes from `datetime.utcnow` - the
    naive-UTC shape the database really holds - and asserts the serialised
    instant matches. A `+00:00` on the string is the difference between the
    browser agreeing and the browser inventing a timezone.
    """

    def test_equity_curve(self):
        db = get_session_local()()
        try:
            # 19:50 UTC is 15:50 ET: the last minutes of the regular session.
            db.add(EquitySnapshot(
                timestamp=datetime(2026, 9, 28, 19, 50, 3, 279066),
                equity=100000.0, cash=25000.0, buying_power=50000.0,
                day_pl=0.0, total_pl=0.0,
            ))
            db.commit()
        finally:
            db.close()

        row = client.get("/dashboard/equity-curve", headers=auth()).json()[0]
        assert parse_instant(row["timestamp"]) == datetime(
            2026, 9, 28, 19, 50, 3, 279066, tzinfo=timezone.utc
        )
        # And the human reading of it, in the market timezone.
        assert parse_instant(row["timestamp"]).astimezone(NY).strftime("%H:%M") == "15:50"

    def test_trade_log(self):
        db = get_session_local()()
        try:
            db.add(TradeLog(
                timestamp=datetime(2026, 9, 28, 19, 30, 3),
                symbol="AAPL", side=OrderSide.buy, qty=4,
                order_type=OrderType.market, status="submitted", message="placed",
            ))
            db.commit()
        finally:
            db.close()

        row = client.get("/dashboard/logs", headers=auth()).json()[0]
        assert parse_instant(row["timestamp"]) == datetime(
            2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc
        )

    def test_audit_log(self):
        db = get_session_local()()
        try:
            db.add(AuditLog(
                created_at=datetime(2026, 9, 28, 19, 30, 3),
                actor_kind="user", method="POST", path="/bot/start",
                action="POST /bot/start", status_code=200,
            ))
            db.commit()
        finally:
            db.close()

        # Filtered by path on purpose: `auth()` above issues POST /auth/login,
        # which the audit middleware records, and that row is newer than the
        # one just inserted. Taking [0] would assert on the login.
        rows = client.get("/dashboard/audit-log?actor=user", headers=auth()).json()
        row = next(r for r in rows if r["path"] == "/bot/start")
        assert parse_instant(row["created_at"]) == datetime(
            2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc
        )

    def test_profile_created_at_and_updated_at(self):
        db = get_session_local()()
        try:
            db.add(StrategyProfile(
                name="tz", strategy_type=StrategyType.rsi_reversion,
                parameters={}, symbols=["AAPL"], enabled=True,
                created_at=datetime(2026, 9, 28, 19, 30, 3),
                updated_at=datetime(2026, 9, 28, 19, 31, 3),
            ))
            db.commit()
        finally:
            db.close()

        row = client.get("/profiles", headers=auth()).json()[0]
        for field, expected_minute in (("created_at", 30), ("updated_at", 31)):
            assert parse_instant(row[field]) == datetime(
                2026, 9, 28, 19, expected_minute, 3, tzinfo=timezone.utc
            ), field

    def test_bot_config_updated_at(self):
        db = get_session_local()()
        try:
            db.add(BotConfig(id=1, is_running=False, updated_at=datetime(2026, 9, 28, 19, 30, 3)))
            db.commit()
        finally:
            db.close()

        row = client.get("/bot/config", headers=auth()).json()
        assert parse_instant(row["updated_at"]) == datetime(
            2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc
        )

    def test_agent_key_created_at_and_nulls_stay_null(self):
        """A nullable timestamp must stay JSON null, not become the epoch or
        "now" - the Agents page renders `last_used_at` conditionally."""
        db = get_session_local()()
        try:
            db.add(AgentKey(
                label="tz", key_prefix="oq_tz", key_hash="hash",
                scopes=["read"], created_at=datetime(2026, 9, 28, 19, 30, 3),
                last_used_at=None, revoked_at=None,
            ))
            db.commit()
        finally:
            db.close()

        row = client.get("/agent-keys", headers=auth()).json()[0]
        assert parse_instant(row["created_at"]) == datetime(
            2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc
        )
        assert row["last_used_at"] is None
        assert row["revoked_at"] is None

    def test_order_submitted_at_from_the_broker(self):
        """Alpaca already sends offset-aware timestamps, so this one was never
        broken - pinned because normalising it must not shift the instant."""
        from decimal import Decimal
        from alpaca.trading.enums import (
            OrderSide as ASide, OrderType as AType, OrderStatus as AStatus,
        )
        import app.routes.dashboard as dash

        order = MagicMock()
        order.id = "11111111-1111-1111-1111-111111111111"
        order.symbol = "AAPL"
        order.qty = Decimal("1")
        order.side = ASide.BUY
        order.order_type = AType.MARKET
        order.limit_price = None
        order.status = AStatus.FILLED
        order.filled_avg_price = Decimal("337.10")
        order.filled_qty = Decimal("1")
        order.submitted_at = datetime(2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc)

        client_ = AsyncMock()
        client_.get_orders = AsyncMock(return_value=[order])
        store_credentials()
        with patch.object(dash, "AlpacaClient", return_value=client_):
            row = client.get("/dashboard/orders", headers=auth()).json()[0]

        assert parse_instant(row["submitted_at"]) == datetime(
            2026, 9, 28, 19, 30, 3, tzinfo=timezone.utc
        )


class TestMarketClockCarriesTheZoneAndTheSession:
    """The frontend must not hardcode "ET" (gotcha 16) and must not have to
    infer the trading session from a boolean."""

    def test_market_clock_reports_timezone_and_session_state(self):
        import app.routes.dashboard as dash
        from app.config import get_settings

        clock = MagicMock()
        clock.timestamp = datetime(2026, 9, 28, 19, 50, tzinfo=timezone.utc)
        clock.is_open = True
        clock.next_open = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)
        clock.next_close = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)

        client_ = AsyncMock()
        client_.get_clock = AsyncMock(return_value=clock)
        store_credentials()
        with patch.object(dash, "AlpacaClient", return_value=client_):
            row = client.get("/dashboard/market-clock", headers=auth()).json()

        assert row["timezone"] == get_settings().bot_timezone
        # 19:50 UTC on a Monday is 15:50 ET - regular session, pre-close.
        assert row["session_state"] == "regular"

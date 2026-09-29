# Tests for the order-status vocabulary that crosses the Alpaca boundary.
#
# Alpaca has 18 order statuses (new, accepted, partially_filled, expired, ...).
# The app has its own 5-value OrderStatus enum used to type the TradeLog.status
# column and two response models. Writing Alpaca's raw status into that column
# succeeds - SQLAlchemy 2.0 emits no CHECK constraint by default - but the row
# then cannot be loaded back, because the ORM coerces the value into the enum
# and raises LookupError.
#
# The visible damage:
#   * GET /dashboard/orders -> 500, Pydantic rejects 'accepted' in response_model
#   * GET /dashboard/logs   -> 500 LookupError on the first logged trade
#   * RiskManager.check_daily_loss's local fallback -> LookupError once pnl exists
#
# Both the first and the second were invisible while the account had no orders.

import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db, get_session_local
from app.models import TradeLog, OrderStatus, OrderSide, OrderType, ApiCredentials
from app.security import encrypt
from app.routes.dashboard import AlpacaClient
from alpaca.trading.enums import (
    OrderStatus as AlpacaOrderStatus,
    OrderSide as AlpacaOrderSide,
    OrderType as AlpacaOrderType,
)

from worker.scheduler import local_order_status


client = TestClient(app)


def override_get_db():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def get_auth_token():
    return client.post("/auth/login", json={"password": "testpass"}).json()["token"]


def store_credentials(db):
    db.query(ApiCredentials).delete()
    db.add(ApiCredentials(
        key_id_encrypted=encrypt("PKTEST"),
        secret_key_encrypted=encrypt("sktest"),
    ))
    db.commit()


def fake_order(status: AlpacaOrderStatus):
    """An order shaped like the ones Alpaca's SDK actually returns.

    Every field uses the SDK's real type, not a convenient one. A plain string
    `id` here is what let a second bug through: Alpaca's `order.id` is a
    uuid.UUID, and the route handed it to `id: str` uncoerced, so
    GET /dashboard/orders 500'd on *every* order regardless of status.
    """
    order = MagicMock()
    order.id = uuid.UUID("eee64176-8cc2-4c3e-a111-9ea593219971")
    order.symbol = "AAPL"
    order.qty = Decimal("1")
    order.side = AlpacaOrderSide.BUY
    order.order_type = AlpacaOrderType.MARKET
    order.limit_price = None
    # A real Alpaca enum member, not a bare string.
    order.status = status
    order.filled_avg_price = None
    order.filled_qty = None
    order.submitted_at = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    return order


class TestDashboardOrdersAcceptsAlpacaVocabulary:
    """GET /dashboard/orders reflects the broker, so it must not narrow the
    broker's vocabulary to the app's own enum."""

    @pytest.mark.parametrize("status", [
        AlpacaOrderStatus.ACCEPTED,
        AlpacaOrderStatus.NEW,
        AlpacaOrderStatus.PARTIALLY_FILLED,
        AlpacaOrderStatus.PENDING_NEW,
        AlpacaOrderStatus.PENDING_CANCEL,
        AlpacaOrderStatus.EXPIRED,
        AlpacaOrderStatus.REPLACED,
        AlpacaOrderStatus.DONE_FOR_DAY,
        AlpacaOrderStatus.CANCELED,
        AlpacaOrderStatus.FILLED,
        AlpacaOrderStatus.REJECTED,
    ])
    def test_endpoint_returns_200_for_each_alpaca_status(self, status):
        db = get_session_local()()
        try:
            store_credentials(db)
        finally:
            db.close()

        mock_client = AsyncMock()
        mock_client.get_orders = AsyncMock(return_value=[fake_order(status)])

        with patch.object(dashboard_module(), "AlpacaClient", return_value=mock_client):
            resp = client.get("/dashboard/orders",
                              headers={"Authorization": f"Bearer {get_auth_token()}"})

        assert resp.status_code == 200, (
            f"status {status.value!r} broke the orders endpoint: {resp.text[:200]}"
        )
        assert resp.json()[0]["status"] == status.value

    def test_response_model_is_not_narrower_than_the_broker(self):
        """Guards the specific mistake: a response_model enum that rejects the
        broker's own values. Asserted on the schema, not just via the endpoint,
        so a future re-narrowing is caught immediately."""
        from app.schemas import OrderResponse

        for field in ("status", "order_type", "side"):
            annotation = OrderResponse.model_fields[field].annotation
            assert annotation is str, (
                f"OrderResponse.{field} is {annotation!r}. It is filled from the "
                f"broker, whose vocabulary is larger than ours; narrowing it here "
                f"turns a normal order into an HTTP 500."
            )

    def test_broker_types_are_coerced_to_the_declared_response_types(self):
        """Alpaca hands back SDK types, not JSON types: `id` is a uuid.UUID,
        `qty` a Decimal, the enums are enum members. Every field the response
        model declares as a JSON primitive has to be coerced at the route, or
        Pydantic v2 rejects it and the endpoint 500s. This asserts the whole
        payload is real JSON, which is the contract the frontend relies on."""
        db = get_session_local()()
        try:
            store_credentials(db)
        finally:
            db.close()

        mock_client = AsyncMock()
        order = fake_order(AlpacaOrderStatus.ACCEPTED)
        order.qty = Decimal("3")
        order.limit_price = Decimal("101.28")
        order.filled_avg_price = Decimal("101.28")
        order.filled_qty = Decimal("3")
        mock_client.get_orders = AsyncMock(return_value=[order])

        with patch.object(dashboard_module(), "AlpacaClient", return_value=mock_client):
            resp = client.get("/dashboard/orders",
                              headers={"Authorization": f"Bearer {get_auth_token()}"})

        assert resp.status_code == 200, resp.text[:300]
        row = resp.json()[0]

        # id must arrive as a string, because the frontend keys its order list
        # on it (there is no per-order cancel; the kill switch cancels all).
        assert isinstance(row["id"], str)
        assert row["id"] == "eee64176-8cc2-4c3e-a111-9ea593219971"
        for field in ("qty", "limit_price", "filled_price", "filled_qty"):
            assert isinstance(row[field], (int, float)), f"{field} is {type(row[field])}"
        assert row["side"] == "buy"
        assert row["order_type"] == "market"
        assert row["status"] == "accepted"
        # submitted_at must be JSON, not a datetime object
        assert isinstance(row["submitted_at"], str)


def dashboard_module():
    import app.routes.dashboard as mod
    return mod


class TestWorkerWritesALoadableRow:
    """The call site, not just the mapping.

    `local_order_status` being correct is worthless if `_process_signal` does
    not use it. This drives the real method and then reads the row back the
    way /dashboard/logs does, which is the only way to catch a regression at
    the call site - a test on the mapping function alone passes happily while
    the worker writes `order.status` verbatim.
    """

    def _run_with_broker_status(self, status, session):
        import asyncio

        from app.bot.risk import RiskManager
        from app.bot.strategies.base import Signal
        from app.models import BotConfig, StrategyProfile, StrategyType
        from worker.scheduler import BotWorker

        db = session()
        try:
            db.add(BotConfig(id=1, is_running=False))
            profile = StrategyProfile(
                name="p", strategy_type=StrategyType.sma_crossover,
                parameters={}, symbols=["AAPL"],
                risk_max_position_pct=0.1, risk_max_daily_loss_pct=0.05,
                risk_max_concurrent_positions=5, enabled=True,
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)

            worker = BotWorker()
            worker.db = db

            order = fake_order(status)
            alpaca = AsyncMock()
            alpaca.submit_order = AsyncMock(return_value=order)

            signal = Signal(
                symbol="AAPL", side="buy", qty=1, order_type="market",
                estimated_price=337.0,
            )
            asyncio.run(worker._process_signal(
                signal=signal, profile=profile, alpaca=alpaca,
                investable_equity=10000.0,
                current_positions_count=0, current_position_symbols=set(),
                risk_manager=RiskManager(db), daily_loss_pct=0.0,
            ))
        finally:
            db.close()

    @pytest.mark.parametrize("status", [
        AlpacaOrderStatus.ACCEPTED,
        AlpacaOrderStatus.NEW,
        AlpacaOrderStatus.PARTIALLY_FILLED,
        AlpacaOrderStatus.PENDING_NEW,
        AlpacaOrderStatus.FILLED,
        AlpacaOrderStatus.CANCELED,
        AlpacaOrderStatus.REJECTED,
    ])
    def test_row_written_by_the_worker_can_be_read_back(self, status):
        """The exact failure being guarded: the commit succeeds, but the ORM
        then cannot load the row, so the Activity tab 500s. Reading it back
        through the ORM is what surfaces the bug."""
        SessionLocal = get_session_local()
        self._run_with_broker_status(status, SessionLocal)

        db = SessionLocal()
        try:
            # Exactly one row. A broken log write used to land the real order
            # fine and *then* fail the commit, so the except branch added a
            # second row saying "Order failed" - two rows, and a lie.
            rows = db.query(TradeLog).all()
            assert len(rows) == 1, (
                f"expected one log row, got {len(rows)}: "
                f"{[(r.status, r.message) for r in rows]}"
            )
            row = rows[0]
            # Touching .status is the operation that used to raise LookupError.
            assert isinstance(row.status, str)
            assert row.status in {s.value for s in OrderStatus}, (
                f"broker sent {status.value!r}, stored {row.status!r}, which is "
                f"outside the app's vocabulary and cannot be loaded back"
            )
            assert row.status != OrderStatus.error.value
            # str(order.id): a uuid.UUID cannot be bound by sqlite3, which is
            # what made the commit fail after the order was already live.
            assert row.alpaca_order_id == "eee64176-8cc2-4c3e-a111-9ea593219971"
        finally:
            db.close()

    def test_logs_endpoint_can_serve_a_worker_written_row(self):
        """End to end: worker writes it, then the endpoint the Activity tab
        calls serves it."""
        SessionLocal = get_session_local()
        self._run_with_broker_status(AlpacaOrderStatus.ACCEPTED, SessionLocal)

        resp = client.get("/dashboard/logs",
                          headers={"Authorization": f"Bearer {get_auth_token()}"})
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()[0]["status"] == "submitted"


class TestLogsEndpointWithARow:
    """GET /dashboard/logs only returned 200 because the table was empty."""

    def test_logs_survive_a_row_written_from_an_alpaca_status(self):
        """The worker writes status=order.status. Even after the worker maps it,
        a row whose status is outside our enum must not 500 the Activity tab -
        that is the exact failure the empty table was hiding."""
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            db.add(TradeLog(
                symbol="AAPL", side=OrderSide.buy, qty=1,
                order_type=OrderType.market, status="accepted", message="raw"
            ))
            db.commit()
        finally:
            db.close()

        resp = client.get("/dashboard/logs",
                          headers={"Authorization": f"Bearer {get_auth_token()}"})
        assert resp.status_code == 200, f"logs endpoint broke: {resp.text[:200]}"


class TestLocalOrderStatusMapping:
    """The worker must translate the broker's status into the app's own
    vocabulary before writing the row, because the column is typed as that
    enum and cannot be loaded back otherwise."""

    @pytest.mark.parametrize("alpaca,expected", [
        (AlpacaOrderStatus.NEW, OrderStatus.submitted),
        (AlpacaOrderStatus.ACCEPTED, OrderStatus.submitted),
        (AlpacaOrderStatus.ACCEPTED_FOR_BIDDING, OrderStatus.submitted),
        (AlpacaOrderStatus.PENDING_NEW, OrderStatus.submitted),
        (AlpacaOrderStatus.PARTIALLY_FILLED, OrderStatus.submitted),
        (AlpacaOrderStatus.PENDING_CANCEL, OrderStatus.submitted),
        (AlpacaOrderStatus.PENDING_REPLACE, OrderStatus.submitted),
        (AlpacaOrderStatus.PENDING_REVIEW, OrderStatus.submitted),
        (AlpacaOrderStatus.CALCULATED, OrderStatus.submitted),
        (AlpacaOrderStatus.STOPPED, OrderStatus.submitted),
        (AlpacaOrderStatus.SUSPENDED, OrderStatus.submitted),
        (AlpacaOrderStatus.HELD, OrderStatus.submitted),
        (AlpacaOrderStatus.FILLED, OrderStatus.filled),
        (AlpacaOrderStatus.CANCELED, OrderStatus.canceled),
        (AlpacaOrderStatus.EXPIRED, OrderStatus.canceled),
        (AlpacaOrderStatus.REPLACED, OrderStatus.canceled),
        (AlpacaOrderStatus.DONE_FOR_DAY, OrderStatus.canceled),
        (AlpacaOrderStatus.REJECTED, OrderStatus.rejected),
    ])
    def test_every_alpaca_status_maps_into_the_local_enum(self, alpaca, expected):
        assert local_order_status(alpaca) == expected

    def test_every_alpaca_status_is_covered_by_an_assertion(self):
        """If Alpaca adds a status, this forces an explicit decision rather
        than letting it fall through to an unmapped default."""
        covered = {
            AlpacaOrderStatus.NEW, AlpacaOrderStatus.ACCEPTED,
            AlpacaOrderStatus.ACCEPTED_FOR_BIDDING, AlpacaOrderStatus.PENDING_NEW,
            AlpacaOrderStatus.PARTIALLY_FILLED, AlpacaOrderStatus.PENDING_CANCEL,
            AlpacaOrderStatus.PENDING_REPLACE, AlpacaOrderStatus.PENDING_REVIEW,
            AlpacaOrderStatus.CALCULATED, AlpacaOrderStatus.STOPPED,
            AlpacaOrderStatus.SUSPENDED, AlpacaOrderStatus.HELD,
            AlpacaOrderStatus.FILLED, AlpacaOrderStatus.CANCELED,
            AlpacaOrderStatus.EXPIRED, AlpacaOrderStatus.REPLACED,
            AlpacaOrderStatus.DONE_FOR_DAY, AlpacaOrderStatus.REJECTED,
        }
        assert covered == set(AlpacaOrderStatus), (
            "Alpaca order statuses changed; add the new one to "
            "TestLocalOrderStatusMapping and to local_order_status(). "
            f"New: {sorted(s.value for s in set(AlpacaOrderStatus) - covered)}"
        )

    def test_result_is_always_a_real_enum_member(self):
        """The whole point: whatever Alpaca sends, the stored value must be
        loadable by the ORM."""
        for alpaca in AlpacaOrderStatus:
            mapped = local_order_status(alpaca)
            assert isinstance(mapped, OrderStatus)
            assert mapped.value in {s.value for s in OrderStatus}

    def test_tolerates_a_bare_string(self):
        assert local_order_status("accepted") == OrderStatus.submitted
        assert local_order_status("filled") == OrderStatus.filled
        assert local_order_status("something_new_alpaca_invented") == OrderStatus.submitted
        assert local_order_status(None) == OrderStatus.submitted

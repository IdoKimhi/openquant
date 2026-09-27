from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from app.main import app
from app.db import get_db, get_session_local
from app.models import TradeLog, EquitySnapshot, ApiCredentials, BotConfig, StrategyProfile, StrategyType
from app.config import get_settings
from app.security import encrypt
from datetime import datetime

client = TestClient(app)
settings = get_settings()


def override_get_db():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db


def get_auth_token():
    resp = client.post("/auth/login", json={"password": "testpass"})
    return resp.json()["token"]


def test_get_account_no_credentials():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/dashboard/account", headers=headers)
    assert resp.status_code == 400
    assert "No credentials stored" in resp.json()["detail"]


def test_get_account_with_credentials():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(ApiCredentials).delete()
        creds = ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"),
            secret_key_encrypted=encrypt("sktest")
        )
        db.add(creds)
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    with patch('app.routes.dashboard.AlpacaClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_account = MagicMock()
        mock_account.equity = "10000.00"
        mock_account.portfolio_value = "10000.00"
        mock_account.cash = "5000.00"
        mock_account.buying_power = "5000.00"
        mock_account.status = "ACTIVE"
        mock_account.last_equity = "9900.00"
        mock_client.get_account = AsyncMock(return_value=mock_account)
        mock_client.get_day_pl = AsyncMock(return_value=100.0)
        mock_client_class.return_value = mock_client
        
        resp = client.get("/dashboard/account", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["equity"] == 10000.0
        assert resp.json()["portfolio_value"] == 10000.0
        assert resp.json()["cash"] == 5000.0
        assert resp.json()["buying_power"] == 5000.0
        assert resp.json()["day_pl"] == 100.0
        assert resp.json()["status"] == "ACTIVE"


def test_get_positions():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(ApiCredentials).delete()
        creds = ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"),
            secret_key_encrypted=encrypt("sktest")
        )
        db.add(creds)
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    with patch('app.routes.dashboard.AlpacaClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_position = MagicMock()
        mock_position.symbol = "AAPL"
        mock_position.qty = "10"
        mock_position.avg_entry_price = "150.00"
        mock_position.market_value = "1550.00"
        mock_position.cost_basis = "1500.00"
        mock_position.current_price = "155.00"
        mock_position.unrealized_pl = "50.00"
        mock_position.unrealized_plpc = "0.0333"
        mock_position.side.value = "long"
        mock_client.get_positions = AsyncMock(return_value=[mock_position])
        mock_client_class.return_value = mock_client
        
        resp = client.get("/dashboard/positions", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["symbol"] == "AAPL"
        assert resp.json()[0]["qty"] == 10.0
        assert resp.json()[0]["market_value"] == 1550.0
        assert resp.json()[0]["cost_basis"] == 1500.0
        assert resp.json()[0]["unrealized_plpc"] == 0.0333


def test_get_orders():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(ApiCredentials).delete()
        creds = ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"),
            secret_key_encrypted=encrypt("sktest")
        )
        db.add(creds)
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    with patch('app.routes.dashboard.AlpacaClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_order = MagicMock()
        mock_order.id = "order123"
        mock_order.symbol = "AAPL"
        mock_order.qty = "10"
        mock_order.side.value = "buy"
        mock_order.order_type.value = "market"
        mock_order.limit_price = None
        mock_order.status.value = "filled"
        mock_order.filled_avg_price = "150.00"
        mock_order.filled_qty = "10"
        mock_order.submitted_at = datetime.utcnow()
        mock_client.get_orders = AsyncMock(return_value=[mock_order])
        mock_client_class.return_value = mock_client
        
        resp = client.get("/dashboard/orders", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["symbol"] == "AAPL"


def test_get_equity_curve():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        # Add some equity snapshots
        db.query(EquitySnapshot).delete()
        snapshot1 = EquitySnapshot(
            timestamp=datetime.utcnow(),
            equity=10000.0,
            cash=5000.0,
            buying_power=5000.0,
            day_pl=100.0,
            total_pl=200.0
        )
        snapshot2 = EquitySnapshot(
            timestamp=datetime.utcnow(),
            equity=10100.0,
            cash=5100.0,
            buying_power=5000.0,
            day_pl=50.0,
            total_pl=250.0
        )
        db.add_all([snapshot1, snapshot2])
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/dashboard/equity-curve", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_get_logs():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(TradeLog).delete()
        log = TradeLog(
            timestamp=datetime.utcnow(),
            profile_id=1,
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            status="filled",
            message="Test log entry"
        )
        db.add(log)
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/dashboard/logs", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert resp.json()[0]["symbol"] == "AAPL"


def test_get_market_clock():
    # Setup credentials
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(ApiCredentials).delete()
        creds = ApiCredentials(
            key_id_encrypted=encrypt("PKTEST"),
            secret_key_encrypted=encrypt("sktest")
        )
        db.add(creds)
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    with patch('app.routes.dashboard.AlpacaClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_clock = MagicMock()
        mock_clock.timestamp = datetime(2026, 9, 27, 12, 0, 0)
        mock_clock.is_open = True
        mock_clock.next_open = None
        mock_clock.next_close = None
        mock_client.get_clock = AsyncMock(return_value=mock_clock)
        mock_client_class.return_value = mock_client
        
        resp = client.get("/dashboard/market-clock", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["is_open"] is True
        # The dashboard renders `new Date(clock.timestamp)`; omitting it makes
        # date-fns throw and unmounts the page as a blank screen.
        assert resp.json()["timestamp"] is not None
        assert resp.json()["timestamp"].startswith("2026-09-27")
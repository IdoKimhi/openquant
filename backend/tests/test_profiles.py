from fastapi.testclient import TestClient
from app.main import app
from app.db import get_db, get_session_local
from app.models import StrategyProfile, StrategyType
from app.config import get_settings

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


def test_create_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    data = {
        "name": "Test SMA",
        "strategy_type": "sma_crossover",
        "parameters": {"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["AAPL", "MSFT"]
    }
    resp = client.post("/profiles", json=data, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test SMA"
    assert resp.json()["strategy_type"] == "sma_crossover"


def test_list_profiles():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/profiles", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    # First create a profile
    data = {
        "name": "Test Get",
        "strategy_type": "rsi_reversion",
        "parameters": {"period": 14, "oversold": 30, "overbought": 70, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["AAPL"]
    }
    create_resp = client.post("/profiles", json=data, headers=headers)
    profile_id = create_resp.json()["id"]
    
    resp = client.get(f"/profiles/{profile_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == profile_id


def test_update_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    data = {
        "name": "Test Update",
        "strategy_type": "momentum_breakout",
        "parameters": {"lookback": 20, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["NVDA"]
    }
    create_resp = client.post("/profiles", json=data, headers=headers)
    profile_id = create_resp.json()["id"]
    
    resp = client.patch(f"/profiles/{profile_id}", json={"name": "Updated Name"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_delete_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    data = {
        "name": "Test Delete",
        "strategy_type": "sma_crossover",
        "parameters": {"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["AAPL"]
    }
    create_resp = client.post("/profiles", json=data, headers=headers)
    profile_id = create_resp.json()["id"]
    
    resp = client.delete(f"/profiles/{profile_id}", headers=headers)
    assert resp.status_code == 200
    
    # Verify deleted
    get_resp = client.get(f"/profiles/{profile_id}", headers=headers)
    assert get_resp.status_code == 404


def test_activate_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    # Create two profiles
    data1 = {
        "name": "Profile 1",
        "strategy_type": "sma_crossover",
        "parameters": {"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["AAPL"]
    }
    data2 = {
        "name": "Profile 2",
        "strategy_type": "rsi_reversion",
        "parameters": {"period": 14, "oversold": 30, "overbought": 70, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["MSFT"]
    }
    resp1 = client.post("/profiles", json=data1, headers=headers)
    resp2 = client.post("/profiles", json=data2, headers=headers)
    id1 = resp1.json()["id"]
    id2 = resp2.json()["id"]
    
    # Activate first
    resp = client.post(f"/profiles/{id1}/activate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    
    # Verify second is disabled
    get_resp = client.get(f"/profiles/{id2}", headers=headers)
    assert get_resp.json()["enabled"] is False
    
    # Activate second
    resp = client.post(f"/profiles/{id2}/activate", headers=headers)
    assert resp.json()["enabled"] is True
    
    # Verify first is now disabled
    get_resp = client.get(f"/profiles/{id1}", headers=headers)
    assert get_resp.json()["enabled"] is False
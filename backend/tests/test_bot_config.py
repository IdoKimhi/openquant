from fastapi.testclient import TestClient
from app.main import app
from app.db import get_db, get_session_local
from app.models import BotConfig, StrategyProfile, StrategyType
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


def test_get_bot_config():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/api/bot/config", headers=headers)
    assert resp.status_code == 200
    assert "schedule_cron" in resp.json()
    assert "is_running" in resp.json()


def test_update_bot_config():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.patch("/api/bot/config", json={"schedule_cron": "0 9 * * MON-FRI", "market_hours_only": False}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["schedule_cron"] == "0 9 * * MON-FRI"
    assert resp.json()["market_hours_only"] is False


def test_start_stop_bot():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    
    # Create and activate a profile first
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        profile = StrategyProfile(
            name="Test Profile",
            strategy_type=StrategyType.sma_crossover,
            parameters={"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
            risk_max_position_pct=0.1,
            risk_max_daily_loss_pct=0.05,
            risk_max_concurrent_positions=5,
            symbols=["AAPL"],
            enabled=True
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        print(f"Created profile with id: {profile.id}")
        
        # Create or get BotConfig and set active profile
        config = db.query(BotConfig).first()
        if not config:
            config = BotConfig()
            db.add(config)
            db.commit()
            db.refresh(config)
            print(f"Created config with id: {config.id}")
        
        config.active_profile_id = profile.id
        db.commit()
        print(f"Set active_profile_id to: {config.active_profile_id}")
    finally:
        db.close()
    
    # Verify profile exists
    db = SessionLocal()
    try:
        profile = db.query(StrategyProfile).filter(StrategyProfile.name == "Test Profile").first()
        print(f"Profile found: {profile}")
        if profile:
            config = db.query(BotConfig).first()
            print(f"Config active_profile_id: {config.active_profile_id if config else 'No config'}")
    finally:
        db.close()
    
    # Start
    resp = client.post("/api/bot/start", headers=headers)
    print(f"Start response: {resp.status_code} - {resp.json()}")
    assert resp.status_code == 200
    assert resp.json()["is_running"] is True
    
    # Stop
    resp = client.post("/api/bot/stop", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_running"] is False
    
    # Pause
    resp = client.post("/api/bot/pause", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_running"] is False


def test_start_bot_requires_active_profile():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    
    # First, clear any active profile
    from app.db import get_session_local
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        config = db.query(BotConfig).first()
        if config:
            config.active_profile_id = None
            config.is_running = False
            db.commit()
    finally:
        db.close()
    
    # Try to start without active profile
    resp = client.post("/api/bot/start", headers=headers)
    assert resp.status_code == 400
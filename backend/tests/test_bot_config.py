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
    resp = client.get("/bot/config", headers=headers)
    assert resp.status_code == 200
    assert "schedule_cron" in resp.json()
    assert "is_running" in resp.json()


def test_update_bot_config():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.patch("/bot/config", json={"schedule_cron": "0 9 * * MON-FRI", "market_hours_only": False}, headers=headers)
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
    resp = client.post("/bot/start", headers=headers)
    print(f"Start response: {resp.status_code} - {resp.json()}")
    assert resp.status_code == 200
    assert resp.json()["is_running"] is True
    
    # Stop
    resp = client.post("/bot/stop", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_running"] is False
    
    # Pause
    resp = client.post("/bot/pause", headers=headers)
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
    resp = client.post("/bot/start", headers=headers)
    assert resp.status_code == 400

def _mkprofile(name):
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    return client.post("/profiles", json={
        "name": name,
        "strategy_type": "sma_crossover",
        "parameters": {"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        "risk_max_position_pct": 0.1,
        "risk_max_daily_loss_pct": 0.05,
        "risk_max_concurrent_positions": 5,
        "symbols": ["AAPL"],
    }, headers=headers).json()["id"]


def test_patch_active_profile_id_keeps_enabled_in_sync():
    """PATCH /bot/config must not be able to strand the bot on a profile it will not trade.

    The worker requires BOTH conditions to line up:

        StrategyProfile.id == bot_config.active_profile_id,
        StrategyProfile.enabled == True

    PATCHing active_profile_id on its own (which SchedulePage does) left
    `enabled` pointing at a different profile, so the query matched nothing,
    every cycle logged "Active profile not found or disabled, skipping cycle",
    and the bot went quiet with no error surfaced anywhere in the UI. Writing
    active_profile_id now moves the `enabled` flag with it.
    """
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    a, b = _mkprofile("A"), _mkprofile("B")
    client.post(f"/profiles/{a}/activate", headers=headers)

    # Point the bot at B without going through /activate.
    client.patch("/bot/config", json={"active_profile_id": b}, headers=headers)

    cfg = client.get("/bot/config", headers=headers).json()
    assert cfg["active_profile_id"] == b

    profs = {p["id"]: p for p in client.get("/profiles", headers=headers).json()}
    assert profs[b]["enabled"] is True, "the newly active profile must be enabled"
    assert profs[a]["enabled"] is False, "the previous one must be disabled"

    # This is the exact lookup the worker performs each cycle.
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        resolved = db.query(StrategyProfile).filter(
            StrategyProfile.id == cfg["active_profile_id"],
            StrategyProfile.enabled == True,
        ).first()
    finally:
        db.close()
    assert resolved is not None, "worker would skip every cycle if this is None"
    assert resolved.id == b


def test_patch_active_profile_id_null_disables_all():
    """Clearing active_profile_id must not leave a stale 'enabled' profile behind."""
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    a = _mkprofile("Solo")
    client.post(f"/profiles/{a}/activate", headers=headers)

    client.patch("/bot/config", json={"active_profile_id": None}, headers=headers)

    cfg = client.get("/bot/config", headers=headers).json()
    assert cfg["active_profile_id"] is None
    profs = {p["id"]: p for p in client.get("/profiles", headers=headers).json()}
    assert profs[a]["enabled"] is False

from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from app.main import app
from app.db import get_db, get_session_local
from app.models import ApiCredentials, Base
from app.config import get_settings
from app.security import encrypt

client = TestClient(app)
settings = get_settings()

# Create test database
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


def test_store_credentials_encrypts():
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.post("/api/credentials", json={"key_id": "PKTEST", "secret_key": "sktest"}, headers=headers)
    assert resp.status_code == 200
    # Verify stored encrypted
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        creds = db.query(ApiCredentials).first()
        assert creds is not None
        assert creds.key_id_encrypted != "PKTEST"
        assert creds.secret_key_encrypted != "sktest"
        # Verify can decrypt
        from app.security import decrypt
        assert decrypt(creds.key_id_encrypted) == "PKTEST"
        assert decrypt(creds.secret_key_encrypted) == "sktest"
    finally:
        db.close()


def test_credentials_status_no_keys():
    # Clear any existing credentials
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        db.query(ApiCredentials).delete()
        db.commit()
    finally:
        db.close()
    
    headers = {"Authorization": f"Bearer {get_auth_token()}"}
    resp = client.get("/api/credentials/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["has_keys"] is False


def test_test_connection_calls_alpaca():
    # Setup: store credentials first
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
    with patch('app.routes.credentials.AlpacaClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_account = MagicMock()
        mock_account.equity = "10000.00"
        mock_account.buying_power = "5000.00"
        mock_account.status = "ACTIVE"
        mock_client.get_account = AsyncMock(return_value=mock_account)
        mock_client_class.return_value = mock_client
        
        resp = client.post("/api/credentials/test", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"
        assert resp.json()["equity"] == "10000.00"
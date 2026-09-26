from fastapi.testclient import TestClient
from app.main import app
from app.security import hash_password
from app.config import get_settings

client = TestClient(app)
settings = get_settings()


def test_login_returns_token():
    # Use the test password that matches the hash in .env
    resp = client.post("/auth/login", json={"password": "testpass"})
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_login_invalid_password():
    resp = client.post("/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_verify_valid_token():
    login_resp = client.post("/auth/login", json={"password": "testpass"})
    token = login_resp.json()["token"]
    resp = client.get("/auth/verify", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_verify_invalid_token():
    resp = client.get("/auth/verify", headers={"Authorization": "Bearer invalid"})
    assert resp.status_code == 401
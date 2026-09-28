"""Agent keys: scoping, revocation, and the boundaries that must not move.

The failure these guard against is not "the endpoint 500s", it is "an agent
holding a read-only key can flatten the account" or "a revoked key still works".
Both look like a 200 in an integration test and neither is caught by a
typecheck, so they are asserted explicitly here.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_db, get_engine
from app.main import app
from app.models import AgentKey, ApiCredentials, BotConfig, StrategyProfile, StrategyType
from app.authz import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    SCOPE_BOT_CONTROL,
    SCOPE_BOT_KILL,
    SCOPE_CONFIG_WRITE,
    SCOPE_READ,
    agent_key_prefix,
    generate_agent_key,
    hash_agent_key,
)
from app.security import encrypt

Session = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
client = TestClient(app)


def admin_headers():
    resp = client.post("/auth/login", json={"password": "testpass"})
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def mint_key(db, scopes, label="test"):
    """Create an agent key directly in the DB and return the plaintext.

    Goes through the real model + authz hashing rather than the HTTP route, so
    these tests exercise authorization itself and not the admin-only endpoint
    that issues keys.
    """
    plaintext, prefix, key_hash = generate_agent_key()
    row = AgentKey(label=label, key_prefix=prefix, key_hash=key_hash, scopes=list(scopes))
    assert plaintext.startswith(prefix), "prefix must be a prefix of the key"
    db.add(row)
    db.commit()
    return plaintext


@pytest.fixture
def db():
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def agent(db):
    """A read-only agent key, by far the common case."""
    return mint_key(db, [SCOPE_READ], label="readonly-agent")


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def row_id(db, key):
    """Look a key row up the way authz does - by its prefix, not by a length
    hardcoded here."""
    row = db.query(AgentKey).filter(AgentKey.key_prefix == agent_key_prefix(key)).first()
    assert row is not None, "no AgentKey row found for the minted key"
    return row.id


def seed_profile(db, name="p1", enabled=True):
    profile = StrategyProfile(
        name=name,
        strategy_type=StrategyType.sma_crossover,
        parameters={"fast_period": 10, "slow_period": 20},
        symbols=["AAPL"],
        enabled=enabled,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def seed_credentials(db):
    db.add(ApiCredentials(key_id_encrypted=encrypt("AK"), secret_key_encrypted=encrypt("SK")))
    db.commit()


# --- Key format and storage ------------------------------------------------

def test_generated_key_is_prefixed_and_long():
    plaintext, prefix, key_hash = generate_agent_key()
    assert plaintext.startswith("oq_")
    # 32 bytes of entropy, base64url encoded. Short keys are guessable.
    assert len(plaintext) >= 40
    assert plaintext.startswith(prefix)
    assert len(key_hash) == 64  # sha256 hex


def test_key_hash_is_not_reversible_to_the_key():
    plaintext, _, key_hash = generate_agent_key()
    assert key_hash != plaintext
    assert plaintext not in key_hash


def test_creation_response_contains_plaintext_but_list_does_not():
    resp = client.post("/agent-keys", json={"label": "ci"}, headers=admin_headers())
    assert resp.status_code == 201
    body = resp.json()
    plaintext = body["key"]
    assert plaintext.startswith("oq_")

    listing = client.get("/agent-keys", headers=admin_headers())
    assert listing.status_code == 200
    assert plaintext not in listing.text
    for item in listing.json():
        assert "key" not in item
        # key_hash must never leave the process either.
        assert "key_hash" not in item


def test_two_keys_are_distinct():
    a, _, _ = generate_agent_key()
    b, _, _ = generate_agent_key()
    assert a != b


# --- Scope enforcement -----------------------------------------------------

def test_read_only_key_can_read_dashboard(agent):
    resp = client.get("/dashboard/logs", headers=auth(agent))
    assert resp.status_code == 200


def test_read_only_key_cannot_write_config(agent):
    resp = client.patch("/bot/config", json={"schedule_cron": "*/1 * * * *"}, headers=auth(agent))
    assert resp.status_code == 403
    assert "config:write" in resp.json()["detail"]


def test_read_only_key_cannot_start_the_bot(agent):
    resp = client.post("/bot/start", headers=auth(agent))
    assert resp.status_code == 403
    assert "bot:control" in resp.json()["detail"]


def test_read_only_key_cannot_reach_the_kill_switch(agent, db):
    """The dangerous one. A monitoring agent must not be able to liquidate."""
    seed_credentials(db)
    resp = client.post("/bot/kill-switch", headers=auth(agent))
    assert resp.status_code == 403
    assert "bot:kill" in resp.json()["detail"]


def test_bot_control_key_can_start_but_still_not_kill(db):
    seed_profile(db)
    config = BotConfig(active_profile_id=db.query(StrategyProfile).first().id)
    db.add(config)
    db.commit()

    key = mint_key(db, [SCOPE_READ, SCOPE_BOT_CONTROL], label="controller")
    assert client.post("/bot/start", headers=auth(key)).status_code == 200
    assert client.post("/bot/stop", headers=auth(key)).status_code == 200
    # control does not imply kill.
    assert client.post("/bot/kill-switch", headers=auth(key)).status_code == 403


def test_config_write_key_can_create_a_profile(db):
    key = mint_key(db, [SCOPE_READ, SCOPE_CONFIG_WRITE], label="configurator")
    resp = client.post(
        "/profiles",
        json={
            "name": "agent-made",
            "strategy_type": "sma_crossover",
            "parameters": {"fast_period": 10, "slow_period": 20},
            "symbols": ["MSFT"],
        },
        headers=auth(key),
    )
    assert resp.status_code == 200
    assert db.query(StrategyProfile).filter(StrategyProfile.name == "agent-made").first()


def test_kill_scope_is_never_granted_by_default():
    assert SCOPE_BOT_KILL not in DEFAULT_SCOPES
    assert DEFAULT_SCOPES == (SCOPE_READ,)


def test_new_key_defaults_to_read_only():
    resp = client.post("/agent-keys", json={"label": "defaults"}, headers=admin_headers())
    assert resp.status_code == 201
    assert resp.json()["scopes"] == [SCOPE_READ]


def test_unknown_scope_is_rejected():
    resp = client.post(
        "/agent-keys",
        json={"label": "sneaky", "scopes": [SCOPE_READ, "root"]},
        headers=admin_headers(),
    )
    assert resp.status_code == 422


def test_empty_scope_list_is_rejected():
    """A key that authenticates and does nothing is a broken integration, not a
    deliberate restriction."""
    resp = client.post("/agent-keys", json={"label": "empty", "scopes": []}, headers=admin_headers())
    assert resp.status_code == 422


def test_scope_catalogue_lists_every_scope():
    resp = client.get("/agent-keys/scopes", headers=admin_headers())
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["scopes"]]
    assert names == list(ALL_SCOPES)
    for scope in resp.json()["scopes"]:
        assert scope["label"] and scope["description"] and scope["danger"]


# --- Human-only boundaries -------------------------------------------------

def test_agent_key_cannot_manage_agent_keys(agent):
    """Otherwise an agent could mint itself a wider key and the scope model is
    decorative."""
    assert client.get("/agent-keys", headers=auth(agent)).status_code == 403
    assert client.post("/agent-keys", json={"label": "escalate"}, headers=auth(agent)).status_code == 403


def test_agent_key_cannot_read_or_write_broker_credentials(agent):
    """Overwriting the Alpaca key would hand the agent the paper account."""
    assert client.get("/credentials/status", headers=auth(agent)).status_code == 403
    assert client.post("/credentials", json={"key_id": "a", "secret_key": "b"}, headers=auth(agent)).status_code == 403


# --- Revocation ------------------------------------------------------------

def test_revoked_key_stops_working(db):
    key = mint_key(db, [SCOPE_READ], label="to-revoke")
    assert client.get("/dashboard/logs", headers=auth(key)).status_code == 200

    db.expire_all()
    key_id = row_id(db, key)
    resp = client.delete(f"/agent-keys/{key_id}", headers=admin_headers())
    assert resp.status_code == 200

    assert client.get("/dashboard/logs", headers=auth(key)).status_code == 401


def test_revoked_key_still_appears_in_the_list(db):
    key = mint_key(db, [SCOPE_READ], label="ghost")
    db.expire_all()
    key_id = row_id(db, key)
    client.delete(f"/agent-keys/{key_id}", headers=admin_headers())

    listing = client.get("/agent-keys", headers=admin_headers()).json()
    row = next(r for r in listing if r["id"] == key_id)
    assert row["label"] == "ghost"
    assert row["revoked_at"] is not None


def test_revoking_twice_is_idempotent(db):
    key = mint_key(db, [SCOPE_READ], label="twice")
    db.expire_all()
    key_id = row_id(db, key)
    assert client.delete(f"/agent-keys/{key_id}", headers=admin_headers()).status_code == 200
    first = db.query(AgentKey).filter(AgentKey.id == key_id).first().revoked_at
    assert client.delete(f"/agent-keys/{key_id}", headers=admin_headers()).status_code == 200
    assert db.query(AgentKey).filter(AgentKey.id == key_id).first().revoked_at == first


def test_revoking_one_key_does_not_affect_another(db):
    keep = mint_key(db, [SCOPE_READ], label="keep")
    drop = mint_key(db, [SCOPE_READ], label="drop")
    db.expire_all()
    drop_id = row_id(db, drop)
    client.delete(f"/agent-keys/{drop_id}", headers=admin_headers())
    assert client.get("/dashboard/logs", headers=auth(keep)).status_code == 200


def test_revoke_unknown_id_is_404():
    assert client.delete("/agent-keys/99999", headers=admin_headers()).status_code == 404


# --- Bad tokens ------------------------------------------------------------

def test_forged_key_is_rejected(agent):
    assert client.get("/dashboard/logs", headers=auth("oq_totallymadeupkeyvalue")).status_code == 401


def test_truncated_key_is_rejected(agent):
    assert client.get("/dashboard/logs", headers=auth(agent[:-4])).status_code == 401


def test_key_with_extra_suffix_is_rejected(agent):
    assert client.get("/dashboard/logs", headers=auth(agent + "x")).status_code == 401


def test_admin_session_still_works_everywhere():
    """The principal refactor must not lock the human out of anything."""
    h = admin_headers()
    assert client.get("/dashboard/logs", headers=h).status_code == 200
    assert client.get("/bot/config", headers=h).status_code == 200
    assert client.get("/profiles", headers=h).status_code == 200
    assert client.get("/credentials/status", headers=h).status_code == 200
    assert client.get("/agent-keys", headers=h).status_code == 200
    # and can still do the powerful things without any scope grant
    assert client.post("/bot/stop", headers=h).status_code == 200


def test_admin_session_is_not_treated_as_an_agent_key():
    token = client.post("/auth/login", json={"password": "testpass"}).json()["token"]
    assert not token.startswith("oq_")


def test_agent_key_does_not_pretend_to_be_a_user(db):
    """A JWT minted for the admin must not be accepted as an agent key, and an
    agent key must not be accepted where a user is required."""
    key = mint_key(db, [SCOPE_READ], label="x")
    assert client.get("/agent-keys", headers=auth(key)).status_code == 403


# --- Observability ---------------------------------------------------------

def test_last_used_at_is_recorded(db):
    key = mint_key(db, [SCOPE_READ], label="used")
    db.expire_all()
    assert client.get("/dashboard/logs", headers=auth(key)).status_code == 200
    db.expire_all()
    row = db.query(AgentKey).filter(AgentKey.key_prefix == agent_key_prefix(key)).first()
    assert row.last_used_at is not None


def test_unused_key_has_null_last_used_at():
    resp = client.post("/agent-keys", json={"label": "unused"}, headers=admin_headers())
    row = next(r for r in client.get("/agent-keys", headers=admin_headers()).json() if r["id"] == resp.json()["id"])
    assert row["last_used_at"] is None


# --- Scope bookkeeping invariants -----------------------------------------

def test_every_scope_is_documented():
    from app.authz import SCOPE_INFO
    assert set(SCOPE_INFO) == set(ALL_SCOPES)


def test_scopes_are_returned_in_canonical_order():
    resp = client.post(
        "/agent-keys",
        json={"label": "order", "scopes": [SCOPE_BOT_KILL, SCOPE_READ]},
        headers=admin_headers(),
    )
    assert resp.json()["scopes"] == [SCOPE_READ, SCOPE_BOT_KILL]

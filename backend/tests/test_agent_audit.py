"""Audit trail: who changed what, and which key they used.

`AgentKey.last_used_at` answers "was this key ever used", which cannot tell a
key that started the bot from one that read the account, and stops recording
the moment the key is revoked. That is the gap these tests close: after the
fact, "did an agent do this" has to be answerable, and *which* agent has to be
answerable too, or revoking one key tells you nothing about what it did while
it was live.

The rows are read back through the ORM, not raw SQL - a committed row that the
ORM cannot load is the bug class in gotcha 10, and a test asserting on a
materialised dict would pass straight through it.
"""

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_engine
from app.main import app
from app.models import AgentKey, AuditLog, StrategyProfile, StrategyType
from app.authz import (
    SCOPE_CONFIG_WRITE,
    SCOPE_BOT_CONTROL,
    SCOPE_READ,
    agent_key_prefix,
    generate_agent_key,
)

Session = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
client = TestClient(app)


def admin_headers():
    resp = client.post("/auth/login", json={"password": "testpass"})
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def mint_key(db, scopes, label="test"):
    plaintext, prefix, key_hash = generate_agent_key()
    row = AgentKey(label=label, key_prefix=prefix, key_hash=key_hash, scopes=list(scopes))
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
def writer_key(db):
    """Every scope except `bot:kill`.

    The realistic worst case for a non-human caller, and the one most worth
    being able to audit. Deliberately *not* all four: the kill switch gets its
    own assertions, because a key that can flatten the account and a key that
    can only reconfigure are different risks.
    """
    return mint_key(
        db, [SCOPE_READ, SCOPE_CONFIG_WRITE, SCOPE_BOT_CONTROL], label="writer-agent"
    )


def seed_profile(db, name="audited"):
    profile = StrategyProfile(
        name=name,
        strategy_type=StrategyType.sma_crossover,
        parameters={"fast_period": 10, "slow_period": 30, "position_size_pct": 0.1},
        risk_max_position_pct=0.1,
        risk_max_daily_loss_pct=0.05,
        risk_max_concurrent_positions=5,
        symbols=["AAPL"],
        enabled=True,
    )
    db.add(profile)
    db.commit()
    return profile


def _snapshot(rows):
    return [
        {c.name: getattr(row, c.name) for c in AuditLog.__table__.columns}
        for row in rows
    ]


def audit_rows(**filters):
    """Materialised audit entries, optionally filtered on exact column values.

    Dicts, not ORM rows: anything handed back after `db.close()` raises
    DetachedInstanceError on first attribute access, so returning the row would
    make the failure look like a broken audit table.
    """
    session = Session()
    try:
        query = session.query(AuditLog)
        for column, value in filters.items():
            query = query.filter(getattr(AuditLog, column) == value)
        return _snapshot(query.order_by(AuditLog.id).all())
    finally:
        session.close()


class TestWhatGetsRecorded:
    def test_agent_action_records_its_own_identity(self, db, writer_key):
        """The whole point: an agent's write is not anonymous, and is not the
        admin's. Without actor_kind + key_id, revoking a key leaves no way to
        tell what it did while it was live."""
        assert client.post("/bot/stop", headers=auth(writer_key)).status_code == 200

        rows = audit_rows(path="/bot/stop")
        assert len(rows) == 1, f"expected one entry, got {rows}"
        row = rows[0]
        assert row["actor_kind"] == "agent"
        assert row["actor_label"] == "writer-agent"
        assert row["key_id"] is not None
        assert row["method"] == "POST"
        assert row["path"] == "/bot/stop"
        assert row["action"] == "/bot/stop"
        assert row["status_code"] == 200
        assert isinstance(row["created_at"], datetime)

    def test_human_action_is_recorded_as_the_admin(self, db):
        """Recorded too, and distinguished. An audit trail that only shows agent
        activity cannot answer "was that the human or the bot?"."""
        client.post("/bot/stop", headers=admin_headers())

        row = audit_rows(path="/bot/stop")[0]
        assert row["actor_kind"] == "user"
        assert row["actor_label"] == "admin"
        assert row["key_id"] is None, "the admin has no agent key row"

    def test_reads_are_not_recorded(self, db, writer_key):
        """The dashboard polls on an interval. Auditing GETs buries the actions
        that matter under noise and grows the table forever."""
        assert client.get("/profiles", headers=auth(writer_key)).status_code == 200
        assert client.get("/bot/config", headers=auth(writer_key)).status_code == 200
        assert audit_rows() == []

    def test_refused_agent_action_is_recorded(self, db):
        """The 403 is the interesting row.

        The route never ran, so nothing downstream logs the attempt. A read-only
        key poking the kill switch is precisely what an audit trail exists to
        answer, and it is invisible unless the refusal is recorded.
        """
        readonly = mint_key(db, [SCOPE_READ], label="readonly-agent")

        assert client.post("/bot/kill-switch", headers=auth(readonly)).status_code == 403

        row = audit_rows(path="/bot/kill-switch")[0]
        assert row["actor_kind"] == "agent"
        assert row["actor_label"] == "readonly-agent"
        assert row["status_code"] == 403

    def test_unauthenticated_mutation_is_recorded_as_anonymous(self, db):
        # HTTPBearer answers 403 when the header is missing entirely.
        assert client.post("/bot/stop").status_code == 403

        row = audit_rows(path="/bot/stop")[0]
        assert row["actor_kind"] == "anonymous"
        assert row["actor_label"] is None
        assert row["key_id"] is None
        assert row["status_code"] == 403

    def test_revoked_key_attempt_is_recorded_as_anonymous(self, db):
        """A revoked key authenticates to None, so the attempt is unattributed
        - which is the point: the trail shows the pressure even though the key
        is dead."""
        dead = mint_key(db, [SCOPE_READ], label="revoked-agent")
        row = db.query(AgentKey).filter(
            AgentKey.key_prefix == agent_key_prefix(dead)
        ).first()
        row.revoked_at = datetime.utcnow()
        db.commit()

        assert client.post("/bot/stop", headers=auth(dead)).status_code == 401

        entry = audit_rows(path="/bot/stop")[0]
        assert entry["actor_kind"] == "anonymous"
        assert entry["status_code"] == 401

    def test_the_audited_path_says_which_profile(self, db, writer_key):
        """`action` is the route template, `path` is what was actually called.
        One alone is not enough: the template loses the id, the concrete path
        explodes into a new value per profile."""
        seed_profile(db, "alpha")
        beta = seed_profile(db, "beta")

        assert client.post(f"/profiles/{beta.id}/activate", headers=auth(writer_key)).status_code == 200

        row = audit_rows(path=f"/profiles/{beta.id}/activate")[0]
        assert row["action"] == "/profiles/{profile_id}/activate"
        assert row["actor_label"] == "writer-agent"


class TestRecordedDetail:
    def test_route_supplied_summary_is_stored(self, db, writer_key):
        """`method + path` says a config changed but not to what. The route
        attaches the after-state explicitly, because capturing request bodies
        wholesale is how credential material ends up in a log file."""
        profile = seed_profile(db, "switch-target")

        resp = client.patch(
            "/bot/config",
            json={"active_profile_id": profile.id, "schedule_cron": "0 10 * * MON-FRI"},
            headers=auth(writer_key),
        )
        assert resp.status_code == 200

        detail = json.loads(audit_rows(path="/bot/config")[0]["detail"])
        assert detail["active_profile_id"] == profile.id
        assert detail["schedule_cron"] == "0 10 * * MON-FRI"

    def test_credentials_never_reach_the_audit_log(self, db):
        """`POST /credentials` is audited as an action and nothing more.

        This is the reason detail is opt-in per route rather than "log the
        body": a body-capturing audit trail writes the Alpaca secret key to
        disk in plaintext, in a table the `read` scope can read back.
        """
        # `POST /credentials` validates with the broker before it writes (issue
        # #7), so that call has to be faked here too. The account stub carries
        # the SDK's real types - `equity` is a `Decimal`, never a `str` - for
        # the reason in gotcha 5b: a convenient stub hides the coercion
        # boundary, and this very stub is what hid a 500 on
        # `POST /credentials/test` for a release.
        with patch("app.routes.credentials.AlpacaClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_account = MagicMock()
            mock_account.equity = Decimal("10000.00")
            mock_account.buying_power = Decimal("5000.00")
            mock_account.status = "ACTIVE"
            mock_client.get_account = AsyncMock(return_value=mock_account)
            mock_client_class.return_value = mock_client

            resp = client.post(
                "/credentials",
                json={"key_id": "PKXXXXXXXXXXXX", "secret_key": "SUPERSECRETVALUE"},
                headers=admin_headers(),
            )
            assert resp.status_code == 200

        rows = audit_rows()
        assert any(r["path"] == "/credentials" for r in rows), "the write itself must be audited"
        blob = json.dumps(rows, default=str)
        assert "SUPERSECRETVALUE" not in blob
        assert "PKXXXXXXXXXXXX" not in blob

    def test_a_broken_audit_write_does_not_fail_the_action(self, db, writer_key, monkeypatch):
        """The trail is evidence, not a dependency.

        By the time the entry is written the action has already been applied.
        If the insert raises - a locked SQLite file, or a backend image older
        than the table - failing the request would turn a logging fault into a
        trading fault, which is exactly the confusion gotcha 11 describes.
        """

        def boom():
            raise RuntimeError("audit storage unavailable")

        monkeypatch.setattr("app.audit.get_session_local", boom)

        assert client.post("/bot/stop", headers=auth(writer_key)).status_code == 200
        assert client.get("/bot/config", headers=auth(writer_key)).json()["is_running"] is False


class TestReadingTheTrail:
    def test_a_read_only_key_can_read_the_trail(self, db, writer_key):
        """Visibility is not a separate permission. A monitoring agent that can
        see the account can see who moved the bot."""
        client.post("/bot/stop", headers=auth(writer_key))

        body = client.get("/dashboard/audit-log", headers=auth(writer_key)).json()
        assert body
        assert body[0]["actor_label"] == "writer-agent"

    def test_reading_requires_the_read_scope(self, db):
        scopeless = mint_key(db, [], label="scopeless")
        assert client.get("/dashboard/audit-log", headers=auth(scopeless)).status_code == 403
        assert client.get("/dashboard/audit-log", headers=admin_headers()).status_code == 200

    def test_admin_gets_newest_first_and_can_filter_by_actor(self, db):
        readonly = mint_key(db, [SCOPE_READ], label="reader")
        client.post("/bot/stop", headers=auth(readonly))
        client.post("/bot/stop", headers=admin_headers())

        rows = client.get("/dashboard/audit-log", headers=admin_headers()).json()
        assert rows[0]["id"] > rows[-1]["id"], "must be newest first"
        assert any(r["actor_kind"] == "user" for r in rows)

        agent_only = client.get(
            "/dashboard/audit-log", params={"actor": "agent"}, headers=admin_headers()
        ).json()
        assert agent_only
        assert all(r["actor_kind"] == "agent" for r in agent_only)

    def test_the_trail_survives_revocation_of_the_key_that_wrote_it(self, db):
        """Revocation is soft, so the row stays. Recording only on
        `last_used_at` would mean the evidence stops at the moment you most
        want it."""
        dead = mint_key(db, [SCOPE_BOT_CONTROL], label="short-lived")
        client.post("/bot/stop", headers=auth(dead))

        row = db.query(AgentKey).filter(
            AgentKey.key_prefix == agent_key_prefix(dead)
        ).first()
        row.revoked_at = datetime.utcnow()
        db.commit()
        db.close()

        body = client.get("/dashboard/audit-log", headers=admin_headers()).json()
        entries = [r for r in body if r["actor_label"] == "short-lived"]
        assert entries, "the action must remain attributable after revocation"
        assert entries[0]["path"] == "/bot/stop"

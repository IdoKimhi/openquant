"""Rate limiting on agent keys.

An agent key is a long-lived bearer token with no other brake. `bot:control`
lets a holder stop and start the bot as fast as the request round-trip allows,
and a leaked `read` key can poll the dashboard endpoints forever. Neither shows
up as a bug - both look exactly like a working API - so the boundaries are
asserted here rather than inferred from the code.

Two things this pins that are easy to get wrong:

- The admin session is deliberately *not* limited. One human in one browser
  cannot be the threat model, and a limiter that can lock the owner out of
  their own bot is a worse failure than the abuse it prevents.
- A 429 must be distinguishable from a 403 by its `detail`, because a 403 with
  no detail means the Authorization header never arrived, and the axios
  interceptor is entitled to treat that differently (gotcha 4).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_engine
from app.main import app
from app.models import AgentKey
from app.authz import SCOPE_BOT_CONTROL, SCOPE_READ, generate_agent_key, rate_limiter

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


@pytest.fixture(autouse=True)
def small_budgets():
    """Shrink the process-wide limiter so a test can exhaust it in six calls
    rather than six hundred. The limits live on the shared instance, so they
    are restored afterwards or the rest of the suite runs throttled."""
    read, write = rate_limiter.read_limit, rate_limiter.write_limit
    rate_limiter.read_limit, rate_limiter.write_limit = 5, 3
    rate_limiter.reset()
    yield
    rate_limiter.read_limit, rate_limiter.write_limit = read, write
    rate_limiter.reset()


class TestLimiterMechanics:
    # 960.0 is exactly 16 windows of 60s. The window boundaries are aligned to
    # the clock, not opened on a client's first request, so every arithmetic
    # assertion below is stated against a known boundary.
    BOUNDARY = 960.0

    def test_budget_is_consumed_then_refused_then_resets_on_the_boundary(self):
        clock = [self.BOUNDARY]
        limiter = rate_limiter.__class__(
            read_limit=3, write_limit=1, window=60, now=lambda: clock[0]
        )

        assert [limiter.check(1, mutating=False)[0] for _ in range(5)] == [
            True, True, True, False, False
        ]
        clock[0] += 59
        assert limiter.check(1, mutating=False)[0] is False, "still inside the window"
        clock[0] += 1
        assert limiter.check(1, mutating=False)[0] is True

    def test_windows_are_aligned_so_a_caller_cannot_straddle_two_budgets(self):
        """The reason for alignment, which a window-opened-on-first-use
        implementation gets wrong.

        Straddling would let a caller spend the entire limit just before a
        boundary and the entire limit again just after it - twice the intended
        burst, on demand, forever."""
        clock = [self.BOUNDARY]
        limiter = rate_limiter.__class__(
            read_limit=3, write_limit=1, window=60, now=lambda: clock[0]
        )
        for _ in range(3):
            assert limiter.check(1, mutating=False)[0] is True

        clock[0] = self.BOUNDARY + 59.999
        assert limiter.check(1, mutating=False)[0] is False, "one millisecond short of a new window"
        clock[0] = self.BOUNDARY + 60
        assert limiter.check(1, mutating=False)[0] is True, "budget renews on the boundary"

    def test_retry_after_points_at_the_window_boundary(self):
        clock = [self.BOUNDARY]
        limiter = rate_limiter.__class__(
            read_limit=1, write_limit=1, window=60, now=lambda: clock[0]
        )
        limiter.check(1, mutating=False)
        clock[0] += 10

        allowed, limit, retry_after = limiter.check(1, mutating=False)
        assert allowed is False
        assert limit == 1
        assert retry_after == 50, "must not tell the caller to retry immediately"

    def test_budgets_are_per_key_and_per_method(self):
        limiter = rate_limiter.__class__(
            read_limit=2, write_limit=1, window=60, now=lambda: 0.0
        )
        assert limiter.check(1, mutating=True)[0] is True
        assert limiter.check(1, mutating=True)[0] is False
        assert limiter.check(1, mutating=False)[0] is True, (
            "a write must not spend the read budget - a monitoring key and a "
            "control key are separate credentials"
        )
        assert limiter.check(2, mutating=True)[0] is True, (
            "one runaway key must not lock out every other key"
        )

    def test_windows_from_the_past_are_pruned(self):
        """Otherwise a long-lived process accumulates one dead bucket per
        key per window, forever, and the map is the only thing standing
        between a leaked key and an out-of-memory backend."""
        clock = [0.0]
        limiter = rate_limiter.__class__(
            read_limit=1, write_limit=1, window=60, now=lambda: clock[0]
        )
        for key in range(1, 50):
            limiter.check(key, mutating=False)
        assert limiter.active_key_count == 49

        clock[0] += 60
        limiter.check(1, mutating=False)
        assert limiter.active_key_count == 1

    def test_reset_clears_one_key_or_all_of_them(self):
        limiter = rate_limiter.__class__(
            read_limit=1, write_limit=1, window=60, now=lambda: 0.0
        )
        limiter.check(1, mutating=False)
        limiter.check(2, mutating=False)

        limiter.reset(key_id=1)
        assert limiter.check(1, mutating=False)[0] is True
        assert limiter.check(2, mutating=False)[0] is False

        limiter.reset()
        assert limiter.check(2, mutating=False)[0] is True


class TestAgentsAreThrottled:
    def test_read_only_key_is_throttled_after_its_budget(self, db):
        poller = mint_key(db, [SCOPE_READ], label="poller")

        statuses = [client.get("/profiles", headers=auth(poller)).status_code for _ in range(6)]
        assert statuses[:5] == [200] * 5
        assert statuses[5] == 429

    def test_the_write_budget_is_tighter_than_the_read_budget(self, db):
        switcher = mint_key(db, [SCOPE_READ, SCOPE_BOT_CONTROL], label="switcher")

        statuses = [client.post("/bot/stop", headers=auth(switcher)).status_code for _ in range(5)]
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3:] == [429, 429], "start/stop must not be as cheap as a read"

    def test_429_names_the_key_and_carries_retry_after(self, db):
        poller = mint_key(db, [SCOPE_READ], label="poller")
        for _ in range(5):
            client.get("/profiles", headers=auth(poller))

        resp = client.get("/profiles", headers=auth(poller))
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) > 0
        assert "poller" in resp.json()["detail"], (
            "a throttled caller has to know which key is throttled"
        )

    def test_a_throttled_key_does_not_throttle_the_others(self, db):
        noisy = mint_key(db, [SCOPE_READ], label="noisy")
        quiet = mint_key(db, [SCOPE_READ], label="quiet")

        for _ in range(6):
            client.get("/profiles", headers=auth(noisy))
        assert client.get("/profiles", headers=auth(noisy)).status_code == 429
        assert client.get("/profiles", headers=auth(quiet)).status_code == 200

    def test_a_429_is_distinguishable_from_a_missing_header(self, db):
        """Both are "you may not do this", and the frontend treats them very
        differently: a bare 403 means no Authorization header was sent, which
        the axios interceptor must not read as an expired session."""
        poller = mint_key(db, [SCOPE_READ], label="poller")

        missing_header = client.post("/bot/stop")
        for _ in range(5):
            client.get("/profiles", headers=auth(poller))
        throttled = client.get("/profiles", headers=auth(poller))

        assert missing_header.status_code == 403
        assert throttled.status_code == 429
        assert throttled.json()["detail"] != missing_header.json()["detail"]

    def test_forged_tokens_cannot_burn_a_real_keys_budget(self, db):
        """The limiter is keyed on the resolved key id, and a forged token never
        resolves. Were it keyed on the prefix or the raw token, anyone could
        guess under a known prefix and starve a real key - turning a
        read-only limit into a denial of service against the human's bot."""
        real = mint_key(db, [SCOPE_READ], label="real")
        forged = real[:11] + "forged-tail" + "0" * 20

        for _ in range(20):
            assert client.get("/profiles", headers=auth(forged)).status_code == 401

        for _ in range(5):
            assert client.get("/profiles", headers=auth(real)).status_code == 200
        assert client.get("/profiles", headers=auth(real)).status_code == 429

    def test_a_revoked_key_cannot_reach_the_limiter(self, db):
        """Revocation is enforced during authentication, before the throttle.
        A dead key costs a hash comparison, not a rate-limit slot."""
        from datetime import datetime

        from app.authz import agent_key_prefix

        dead = mint_key(db, [SCOPE_READ], label="revoked-agent")
        row = db.query(AgentKey).filter(
            AgentKey.key_prefix == agent_key_prefix(dead)
        ).first()
        row.revoked_at = datetime.utcnow()
        db.commit()

        for _ in range(20):
            assert client.get("/profiles", headers=auth(dead)).status_code == 401


class TestTheAdminIsNotThrottled:
    def test_a_burst_of_reads_is_never_refused(self, db):
        headers = admin_headers()
        for _ in range(20):
            assert client.get("/profiles", headers=headers).status_code == 200

    def test_the_admin_can_still_reach_human_only_routes_while_throttled(self, db):
        """A key that has exhausted its own budget must not lock the human out
        of minting a replacement for it - that is the recovery path."""
        poller = mint_key(db, [SCOPE_READ], label="poller")
        for _ in range(6):
            client.get("/profiles", headers=auth(poller))
        assert client.get("/profiles", headers=auth(poller)).status_code == 429

        assert client.get("/agent-keys", headers=admin_headers()).status_code == 200
        assert client.delete(
            f"/agent-keys/{db.query(AgentKey).filter(AgentKey.label == 'poller').first().id}",
            headers=admin_headers(),
        ).status_code == 200

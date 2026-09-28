"""A read-only request must not write to the database.

`last_used_at` is stamped on the `agent_keys` row on *every* authenticated agent
request. That makes every read a write, and on this stack a write is not cheap:
SQLite in rollback-journal mode takes a whole-database lock and fsyncs on commit,
while the backend and the worker are separate processes sharing one file on a
Docker volume. Concurrent agent reads therefore serialise on the write lock and
blow past the 5s busy timeout - observed live as `GET /profiles` returning 500
`sqlite3.OperationalError: database is locked` under 8-way concurrency, while the
same load with the admin token (which never stamps `last_used_at`) returned 40
clean 200s.

This is exactly the traffic the rate limiter was added to permit: 120 reads/min,
sustained. The limiter was not a brake on the database at all, because
authentication - and therefore the write - happens *before* the limit is
checked. A 429 still took the write lock.

`last_used_at` is a "when was this key last used" convenience for the Agents
page, not authorisation input, and the page renders it to the minute. So it is
written when it is missing or stale, and skipped otherwise.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from app.authz import (
    LAST_USED_WRITE_INTERVAL,
    SCOPE_READ,
    agent_key_prefix,
    generate_agent_key,
    rate_limiter,
)
from app.db import SQLITE_BUSY_TIMEOUT_S, build_engine, get_engine
from app.main import app
from app.models import AgentKey

Session = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
client = TestClient(app)


def auth(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def db():
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def count_commits():
    """Count real database commits made through the engine.

    Engine-level rather than monkeypatching a session, so it catches writes from
    anywhere - the auth dependency, the route, or the audit middleware - which
    is the point. A test that only asserted on `last_used_at` would have passed
    against the old code, because the old code changed it too, just every time.
    """
    total = 0

    def _count(conn):
        nonlocal total
        total += 1

    event.listen(get_engine(), "commit", _count)
    try:
        yield lambda: total
    finally:
        event.remove(get_engine(), "commit", _count)


def mint_key(db, label="last-used"):
    plaintext, prefix, key_hash = generate_agent_key()
    db.add(AgentKey(label=label, key_prefix=prefix, key_hash=key_hash, scopes=[SCOPE_READ]))
    db.commit()
    return plaintext


def row_for(db, plaintext):
    return db.query(AgentKey).filter(AgentKey.key_prefix == agent_key_prefix(plaintext)).one()


def set_last_used(db, plaintext, when):
    row_for(db, plaintext).last_used_at = when
    db.commit()


def get_last_used(db, plaintext):
    # expire so the value comes from the row, not a cached identity-map copy
    db.expire_all()
    return row_for(db, plaintext).last_used_at


def test_first_use_stamps_last_used(db):
    """The field must still be populated - that is the feature."""
    key = mint_key(db)
    assert get_last_used(db, key) is None

    assert client.get("/profiles", headers=auth(key)).status_code == 200
    assert get_last_used(db, key) is not None


def test_recent_read_does_not_rewrite_the_row(db):
    """A second read inside the interval leaves the stored value alone."""
    key = mint_key(db)
    client.get("/profiles", headers=auth(key))

    # Pin to a value well inside the interval so the assertion does not depend
    # on how long the test itself took.
    pinned = datetime.utcnow() - timedelta(seconds=5)
    set_last_used(db, key, pinned)

    assert client.get("/profiles", headers=auth(key)).status_code == 200
    assert get_last_used(db, key) == pinned


def test_stale_read_refreshes_the_row(db):
    """Past the interval it is refreshed again, so a key that goes quiet and
    then comes back reports the truth."""
    key = mint_key(db)
    client.get("/profiles", headers=auth(key))

    stale = datetime.utcnow() - LAST_USED_WRITE_INTERVAL - timedelta(seconds=5)
    set_last_used(db, key, stale)

    assert client.get("/profiles", headers=auth(key)).status_code == 200
    assert get_last_used(db, key) > stale


def test_polling_a_read_endpoint_writes_once_not_forever(db, count_commits):
    """N reads, one write. This is the actual regression."""
    key = mint_key(db)

    before = count_commits()
    for _ in range(25):
        assert client.get("/profiles", headers=auth(key)).status_code == 200

    assert count_commits() - before == 1


def test_throttled_read_writes_nothing(db, count_commits):
    """A 429 must not have written first.

    Authentication stamps `last_used_at` before the limiter runs, so before this
    change a rejected request still took the write lock - which is precisely the
    load the limiter exists to absorb.
    """
    key = mint_key(db)
    client.get("/profiles", headers=auth(key))  # first use stamps

    original = rate_limiter.read_limit
    rate_limiter.read_limit = 1
    try:
        rate_limiter.reset(key_id=row_for(db, key).id)
        client.get("/profiles", headers=auth(key))  # consumes the 1
        assert client.get("/profiles", headers=auth(key)).status_code == 429

        before = count_commits()
        for _ in range(5):
            assert client.get("/profiles", headers=auth(key)).status_code == 429
        assert count_commits() - before == 0
    finally:
        rate_limiter.read_limit = original
        rate_limiter.reset()


def test_engine_is_configured_for_shared_sqlite(tmp_path):
    """The engine must put SQLite in WAL with a per-connection busy timeout.

    Checked against a throwaway file rather than the live engine on purpose: the
    test session has already set WAL on the test database by other means, so
    asserting on it would pass whether or not `app/db.py` did anything.

    WAL is what lets the worker's writes proceed while the backend serves reads.
    In the default rollback journal a reader and a writer block each other across
    the whole database, and with the worker and the backend as separate
    containers sharing one file that is the normal case, not an edge case.

    The second connection is the load-bearing part. `journal_mode` is persistent
    in the file, but `busy_timeout` is per-connection - setting it once on a
    throwaway connection would leave every later connection on the 5s default,
    which is the failure that actually happened.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'probe.db'}")

    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"

    with engine.connect() as conn:
        assert int(conn.execute(text("PRAGMA busy_timeout")).scalar()) >= 5000

    with engine.connect() as conn:
        assert int(conn.execute(text("PRAGMA busy_timeout")).scalar()) == int(
            SQLITE_BUSY_TIMEOUT_S * 1000
        )

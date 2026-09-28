# Test configuration - MUST be imported first to set environment variables
import os
import sys

# Set test environment variables BEFORE any app imports
os.environ["SECRET_ENCRYPTION_KEY"] = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
os.environ["APP_PASSWORD_HASH"] = "$2b$12$Ap6NlSRdAn7GGVacLygKXeH6Lj1k/YW/XHl5jT5OsVQDjmiX9kjYK"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["JWT_EXPIRE_HOURS"] = "24"

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Now import and initialize database
import pytest
from sqlalchemy import text
from app.db import reset_db, init_db, get_engine, Base

# Create tables once at session start
engine = get_engine()
Base.metadata.create_all(bind=engine)

# Test-database only: turn off the per-commit fsync.
#
# WAL + a busy timeout now come from `app/db.py`, because the live database
# needs them too - the worker and the backend are separate containers sharing
# one file, and in the default rollback journal a write blocks every read.
# `synchronous=NORMAL` is still test-only: the default rollback journal fsyncs
# on every commit and this suite makes thousands of them back to back, enough
# that a full run spends most of its time in the filesystem journal (wchan
# `jbd2_log_wait_commit`) rather than running anything. The data is thrown away
# at the end of the run and surviving a power cut mid-test is not a scenario
# this database has to handle.
with engine.connect() as _conn:
    _conn.execute(text("PRAGMA synchronous=NORMAL"))
    _conn.commit()


@pytest.fixture(autouse=True)
def recreate_db():
    """Recreate database tables for each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """The agent rate limiter is process state, not database state.

    SQLite hands the same row ids back after the per-test drop/create above,
    so key id 1 in this test is a different key from key id 1 in the last one -
    but the in-memory bucket keyed on it is not. Without this reset the suite
    throttles itself, and the failure looks like a flaky test rather than a
    leak.
    """
    from app.authz import rate_limiter
    rate_limiter.reset()
    yield
    rate_limiter.reset()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_db():
    """Clean up test database file after session."""
    yield
    import os
    if os.path.exists("test.db"):
        os.remove("test.db")
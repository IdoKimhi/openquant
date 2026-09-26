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
from app.db import reset_db, init_db, get_engine, Base

# Create tables once at session start
engine = get_engine()
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def recreate_db():
    """Recreate database tables for each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_db():
    """Clean up test database file after session."""
    yield
    import os
    if os.path.exists("test.db"):
        os.remove("test.db")
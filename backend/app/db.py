from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

# How long a writer waits for the SQLite write lock before giving up.
#
# The default is 5s, which was not enough: the backend and the worker are
# separate containers sharing one database file, and under concurrent agent
# reads a writer could hold the lock past it, surfacing as a 500
# `sqlite3.OperationalError: database is locked` on a plain `GET /profiles`.
# Waiting is the right answer here because the routes that take this lock are
# synchronous `def`, so FastAPI runs them in a threadpool and a blocked write
# does not stall the event loop.
SQLITE_BUSY_TIMEOUT_S = 15.0

# Lazy initialization - engine created on first use
_engine = None
_SessionLocal = None
Base = declarative_base()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _set_wal(engine: Engine) -> None:
    """Put SQLite in WAL, which is a persistent property of the file.

    WAL lets readers and a writer work at the same time. The default rollback
    journal takes a whole-database lock for the duration of any write, so with
    the worker committing trade logs while the backend serves dashboard reads,
    they block each other by default - and a blocked read eventually gives up
    and returns 500 rather than waiting.

    This is persistent in the database file, so setting it once per engine is
    enough. The busy timeout is *not*, which is why that one is in
    `connect_args` below instead - it has to be applied to every connection.
    """
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()


def build_engine(database_url: str) -> Engine:
    """Create an engine, configuring SQLite for this app's access pattern.

    Factored out of `get_engine` so the configuration is testable against a
    throwaway file. Testing it against the live engine proves nothing, because
    the test session has already put that file into WAL by other means.
    """
    connect_args = {"check_same_thread": False}
    if _is_sqlite(database_url):
        connect_args["timeout"] = SQLITE_BUSY_TIMEOUT_S
    engine = create_engine(database_url, connect_args=connect_args)
    if _is_sqlite(database_url):
        _set_wal(engine)
    return engine


def get_engine():
    global _engine
    if _engine is None:
        _engine = build_engine(get_settings().database_url)
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=get_engine())


def reset_db():
    """Reset database - for testing only."""
    global _engine, _SessionLocal
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
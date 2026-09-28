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


def _render_default(server_default, engine: Engine) -> str:
    """SQL for a column's server default, ready to splice into ADD COLUMN.

    `server_default` is either a raw string (`server_default="1.0"`, which is
    how it is declared on the models here) or a SQLAlchemy expression. A
    string is already the SQL, so it is used verbatim; an expression is
    rendered through the dialect, because how a default has to be quoted is
    dialect-specific and a hand-rolled str() gets it wrong on Postgres.
    """
    arg = getattr(server_default, "arg", server_default)
    if isinstance(arg, str):
        return arg
    compiled = arg.compile(dialect=engine.dialect)
    return compiled.string if hasattr(compiled, "string") else str(compiled)


def ensure_schema(engine: Engine | None = None) -> list[str]:
    """Add columns that exist on the models but not yet in the database file.

    This project has no Alembic environment (alembic is in requirements.txt
    and nothing uses it), so `create_all` is the only thing that runs at
    startup - and `create_all` creates *tables*, never columns. Adding
    `bot_config.capital_allocation_pct` to the model and redeploying would
    therefore have produced a worker and a backend that both crash on the
    first query, with "no such column" as the error.

    Deliberately narrow: ADD COLUMN only, for columns this app added later.
    There is no down-migration and no data backfill beyond the column's
    `server_default`, which is why every added column carries one. Anything
    needing more - a rename, a type change, a backfill - needs real Alembic,
    not this.

    Idempotent, and a no-op on a database that is already current. Returns
    the names it added, so a caller can log them.
    """
    engine = engine or get_engine()
    added: list[str] = []

    with engine.begin() as conn:
        existing_tables = {
            row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table in Base.metadata.sorted_tables:
            # A table that does not exist yet is create_all's job.
            if table.name not in existing_tables:
                continue
            present = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = (
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                    f'{column.type.compile(dialect=engine.dialect)}'
                )
                if column.server_default is not None:
                    # Without this the added column is NULL, and a model default
                    # would then be silently absent from every existing row -
                    # the "it works on a fresh database" bug. Rendered through
                    # the dialect rather than str()'d, because the quoting and
                    # type of a default are dialect-specific.
                    ddl += f" DEFAULT {_render_default(column.server_default, engine)}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")

    return added


def init_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)


def reset_db():
    """Reset database - for testing only."""
    global _engine, _SessionLocal
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
# Tests for the column-add path introduced with bot_config.
#
# This app has no Alembic environment - `alembic` sits in requirements.txt and
# nothing imports it - so `init_db` is the only thing that runs at startup. And
# `create_all` creates tables; it does not touch the columns of a table that
# already exists.
#
# The consequence, which is the reason this file exists: deploying the
# capital_allocation_pct column as a model change alone produces a backend and
# a worker that both die on their first query with "no such column:
# bot_config.capital_allocation_pct", on a database file that holds real
# trades. `ensure_schema` is the fix, and the only way to know it works is to
# build a database in the *old* shape and add the column to it - which is what
# these tests do, because a fresh `create_all` already has the column and
# proves nothing.

import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import build_engine, ensure_schema, get_engine, init_db
from app.models import Base, BotConfig
from worker import scheduler as worker_scheduler
from worker.scheduler import BotWorker


def old_shaped_db(path):
    """A bot_config table as it existed before capital_allocation_pct.

    Written with raw SQL on purpose. Building it through the models would
    create the new column, which is the situation that hides the bug.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE bot_config (
            id INTEGER NOT NULL PRIMARY KEY,
            schedule_cron VARCHAR,
            market_hours_only BOOLEAN,
            active_profile_id INTEGER,
            is_running BOOLEAN,
            updated_at DATETIME
        );
        INSERT INTO bot_config
            (id, schedule_cron, market_hours_only, active_profile_id, is_running, updated_at)
        VALUES (1, '*/5 9-16 * * MON-FRI', 1, NULL, 0, '2026-09-28 10:00:00');
        """
    )
    conn.commit()
    conn.close()


def columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table}")'))}


def table_names(engine):
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }


def sqlite_error(message):
    """The OperationalError SQLAlchemy actually raises, `orig` and all.

    Constructed rather than stringified because `init_db` matches on the
    message; hand-rolling the exception would not exercise the same path a real
    race takes through the driver.
    """
    return OperationalError("CREATE TABLE ...", {}, sqlite3.OperationalError(message))


class TestEnsureSchemaUpgradesAnExistingFile:
    def test_adds_the_missing_column_to_an_existing_table(self, tmp_path):
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        engine = build_engine(f"sqlite:///{path}")

        assert "capital_allocation_pct" not in columns(engine, "bot_config")
        added = ensure_schema(engine)
        assert "bot_config.capital_allocation_pct" in added
        assert "capital_allocation_pct" in columns(engine, "bot_config")

    def test_existing_rows_get_the_model_default_not_null(self, tmp_path):
        """The part that makes the difference between a deploy and an outage.

        SQLite's ADD COLUMN leaves existing rows NULL. The worker then reads
        None and `equity * None` raises TypeError inside the trading cycle, so
        the feature is only half-wired: new rows work, the account you already
        have does not. `server_default` is what closes that gap.
        """
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        engine = build_engine(f"sqlite:///{path}")
        ensure_schema(engine)

        with engine.connect() as conn:
            value = conn.execute(text("SELECT capital_allocation_pct FROM bot_config")).scalar()
        assert value == pytest.approx(1.0), (
            "an existing row was left with NULL, so the worker would compute "
            "equity * None and abort the cycle"
        )

    def test_other_columns_and_rows_are_untouched(self, tmp_path):
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        engine = build_engine(f"sqlite:///{path}")
        ensure_schema(engine)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT schedule_cron, market_hours_only, is_running FROM bot_config")
            ).fetchone()
        assert row == ("*/5 9-16 * * MON-FRI", 1, 0)

    def test_running_twice_changes_nothing(self, tmp_path):
        """It runs on every startup of both containers, so it has to be a
        no-op the second time - an unconditional ALTER TABLE would fail on
        boot and take the worker down."""
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        engine = build_engine(f"sqlite:///{path}")

        ensure_schema(engine)
        assert ensure_schema(engine) == []

    def test_a_fresh_database_needs_nothing(self, tmp_path):
        """`create_all` already made every column, so the upgrade path must
        find nothing to do rather than trying to re-add what it just created."""
        engine = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        Base.metadata.create_all(bind=engine)
        assert ensure_schema(engine) == []

    def test_tables_that_do_not_exist_yet_are_left_to_create_all(self, tmp_path):
        """A brand-new file has no tables at all. `ensure_schema` must not
        invent them - `create_all` owns that - and must not fail trying."""
        engine = build_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        assert ensure_schema(engine) == []

    def test_the_live_test_database_is_current(self):
        """Guards against a model gaining a column with no `server_default`.

        A test-created database has every column, so this only checks the
        declaration - but a missing server_default is precisely what produces
        the NULL-in-existing-rows outage above, and it is invisible until a
        real deployment is upgraded.
        """
        table = Base.metadata.tables["bot_config"]
        column = table.c.capital_allocation_pct
        assert column.server_default is not None, (
            "capital_allocation_pct has no server_default, so ensure_schema "
            "would add it as NULL and every existing row would break the cycle"
        )


# The upgrade path is only half a fix if it runs in one of the two processes
# that share the file. `init_db` is called from the backend's startup hook, and
# the worker - a separate container, its own engine, its own connection pool -
# never called it at all. So the deploy below did what the tests above say it
# does: the backend's `/bot/config` served `capital_allocation_pct` correctly
# while the worker crash-looped on `no such column: bot_config.
# capital_allocation_pct`, raising before it ever scheduled a cycle.
#
# The worker cannot rely on the backend having upgraded first. `depends_on:
# condition: service_started` only waits for the container to spawn; the backend
# runs `init_db` from a startup hook, after that. And `docker compose restart
# worker` on its own starts the worker with nothing else running at all.
def bind_worker_to(path, monkeypatch):
    """Point every database handle the worker has at `path`, and return the engine.

    Two handles, not one. `init_db()` resolves the engine through `app.db`'s
    module global, and `BotWorker` resolves its session through the name it
    imported into `worker.scheduler`. Patching only the latter leaves the
    upgrade running against the shared test database - where the column already
    exists - so the test passes for the wrong reason.
    """
    engine = build_engine(f"sqlite:///{path}")
    monkeypatch.setattr("app.db.get_engine", lambda: engine)
    monkeypatch.setattr(
        worker_scheduler, "get_session_local", lambda: sessionmaker(bind=engine)
    )
    # Signal handlers can only be installed on the main thread's, and replacing
    # pytest's own SIGINT handler is a side effect no test needs.
    monkeypatch.setattr(BotWorker, "_setup_signal_handlers", lambda self: None)
    return engine


class TestTheWorkerUpgradesTheSchemaItself:
    def test_building_a_worker_brings_an_old_database_forward(self, tmp_path, monkeypatch):
        """The exact deployment that produced the crash loop.

        Built against a file in the pre-`capital_allocation_pct` shape, with
        nothing else having touched it - no backend startup hook in the loop,
        because there is not one to wait for.
        """
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        engine = bind_worker_to(path, monkeypatch)

        BotWorker()  # must not raise

        assert "capital_allocation_pct" in columns(engine, "bot_config")

    def test_a_fresh_worker_can_query_bot_config_on_an_old_file(self, tmp_path, monkeypatch):
        """Construction succeeding is not the bar; the first real query is.

        `start()` reads `BotConfig` before it schedules anything, which is where
        the OperationalError surfaced in production - and a construction-only
        check passes even with a null engine, since nothing has been queried.
        """
        path = str(tmp_path / "bot.db")
        old_shaped_db(path)
        bind_worker_to(path, monkeypatch)

        worker = BotWorker()
        config = worker.db.query(BotConfig).first()
        assert config is not None
        # The column the crash named, read as the ORM type the cycle uses.
        assert config.capital_allocation_pct == pytest.approx(1.0)

    def test_the_worker_creates_tables_on_an_empty_database(self, tmp_path, monkeypatch):
        """The other half: an empty file has nothing to ALTER.

        `ensure_schema` deliberately skips tables that do not exist - creating
        them is `create_all`'s job - so a worker started against a brand-new
        volume has to run the create too, or it dies on `no such table`.
        """
        path = str(tmp_path / "empty.db")
        engine = bind_worker_to(path, monkeypatch)

        BotWorker()

        assert {"bot_config", "strategy_profiles", "trade_logs"} <= table_names(engine)


class TestTwoContainersStartingAtOnce:
    def test_init_db_survives_losing_the_create_all_race(self, tmp_path, monkeypatch):
        """Both containers call `init_db` now, and nothing orders them.

        `depends_on: service_started` waits for the container, not for the
        startup hook that calls `init_db`, so both processes can reach
        `create_all` together. `create_all(checkfirst=True)` is a
        read-then-create and has no `IF NOT EXISTS` behind it, so the loser gets
        `OperationalError: table "bot_config" already exists` and dies on
        startup.

        That is not hypothetical either: the worker crash-looped, and Docker
        restarted it repeatedly while the backend was still finishing its own
        boot.
        """
        path = str(tmp_path / "race.db")
        engine = build_engine(f"sqlite:///{path}")
        monkeypatch.setattr("app.db.get_engine", lambda: engine)

        real_create_all = Base.metadata.create_all
        calls = {"n": 0}

        def racing_create_all(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Exactly what the loser of the race sees, before the winner
                # has committed and the table is actually visible to it.
                raise sqlite_error('table "bot_config" already exists')
            return real_create_all(*args, **kwargs)

        monkeypatch.setattr(Base.metadata, "create_all", racing_create_all)

        init_db()

        assert calls["n"] > 1, "init_db gave up instead of retrying"
        assert "bot_config" in table_names(engine)

    def test_init_db_still_raises_on_an_error_that_is_not_the_race(self, tmp_path, monkeypatch):
        """The retry must be narrow.

        Swallowing and retrying every OperationalError turns a genuine
        corruption or permissions problem into a slow, confusing hang, and the
        eventual message no longer matches the cause.
        """
        path = str(tmp_path / "broken.db")
        engine = build_engine(f"sqlite:///{path}")
        monkeypatch.setattr("app.db.get_engine", lambda: engine)

        attempts = {"n": 0}

        def always_disk_io_error(*args, **kwargs):
            attempts["n"] += 1
            raise sqlite_error("disk I/O error")

        monkeypatch.setattr(Base.metadata, "create_all", always_disk_io_error)

        with pytest.raises(OperationalError, match="disk I/O error"):
            init_db()
        assert attempts["n"] == 1, "an unrelated OperationalError was retried"

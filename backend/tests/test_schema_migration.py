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

from app.db import build_engine, ensure_schema, get_engine
from app.models import Base


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

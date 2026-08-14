"""Guard against the migrations drifting away from the models.

Without this, someone adds a column, the tests pass (they use
`SQLModel.metadata.create_all`), and the deployment — which runs Alembic —
quietly ends up with a different schema.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401  registers the tables
from alembic import command

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Diff entries that are not real drift. SQLite reports no server defaults and
#: renders some types loosely, so type/default comparisons are noise here.
IGNORED_DIFF_KINDS = {"modify_type", "modify_default", "modify_nullable"}


def _alembic_config(url: str) -> Config:
    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_apply_cleanly_and_match_the_models():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "migrated.db"
        sync_url = f"sqlite:///{path}"

        # env.py builds an async engine from settings, so point it at the temp db.
        import os

        previous = os.environ.get("KP_DATABASE_URL")
        os.environ["KP_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            command.upgrade(_alembic_config(sync_url), "head")
        finally:
            if previous is None:
                os.environ.pop("KP_DATABASE_URL", None)
            else:
                os.environ["KP_DATABASE_URL"] = previous
            get_settings.cache_clear()

        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, SQLModel.metadata)
        engine.dispose()

    real = [
        entry
        for entry in diff
        if not (isinstance(entry, tuple) and entry and entry[0] in IGNORED_DIFF_KINDS)
    ]
    assert real == [], (
        "The migrations no longer match the models. Run "
        "`uv run alembic revision --autogenerate -m '...'` and review the diff.\n"
        f"{real}"
    )


def test_every_model_table_is_created_by_the_migration():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "migrated.db"
        sync_url = f"sqlite:///{path}"

        import os

        os.environ["KP_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            command.upgrade(_alembic_config(sync_url), "head")
        finally:
            os.environ.pop("KP_DATABASE_URL", None)
            get_settings.cache_clear()

        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            existing = set(context.connection.dialect.get_table_names(conn))
        engine.dispose()

    expected = set(SQLModel.metadata.tables)
    missing = expected - existing
    assert not missing, f"migration does not create: {sorted(missing)}"


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        ("rally_events", "uq_event_client_id"),
        ("rally_events", "uq_event_seq"),
        ("games", "uq_game_number"),
        ("matches", "uq_match_draw_slot"),
    ],
)
def test_sync_critical_constraints_are_declared(table, constraint):
    """These are what make offline sync idempotent — losing one would let a
    retried batch double-count rallies."""
    names = {
        c.name for c in SQLModel.metadata.tables[table].constraints if c.name
    }
    assert constraint in names


def _postgres_migration_sql() -> str:
    """The SQL the whole migration chain emits for Postgres, without a server.

    Alembic's offline mode renders DDL for a target dialect from the scripts
    alone, which is the only way to inspect Postgres-specific schema decisions
    from a suite that runs on SQLite.
    """
    import io
    import os
    from contextlib import redirect_stdout

    from app.core.config import get_settings

    previous = os.environ.get("KP_DATABASE_URL")
    os.environ["KP_DATABASE_URL"] = "postgresql+psycopg://u:p@localhost/db"
    get_settings.cache_clear()
    try:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command.upgrade(
                _alembic_config("postgresql+psycopg://u:p@localhost/db"),
                "head",
                sql=True,
            )
        return buffer.getvalue()
    finally:
        if previous is None:
            os.environ.pop("KP_DATABASE_URL", None)
        else:
            os.environ["KP_DATABASE_URL"] = previous
        get_settings.cache_clear()


def _model_enum_type_names() -> set[str]:
    import sqlalchemy as sa

    names = set()
    for table in SQLModel.metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, sa.Enum) and column.type.name:
                names.add(column.type.name)
    return names


def test_every_enum_column_becomes_a_native_postgres_type():
    """A Postgres-only failure mode that SQLite cannot show.

    SQLModel maps an Enum field to a *native* Postgres enum, so every query
    binds the parameter as `$1::thattype`. A migration that creates the column
    as VARCHAR instead passes the whole SQLite suite and then fails in
    deployment with `type "..." does not exist` — which is precisely what
    a1c4e7b92f10 did to `tournaments.kind`.
    """
    sql = _postgres_migration_sql()
    missing = [
        name for name in sorted(_model_enum_type_names())
        if f"CREATE TYPE {name}" not in sql and f"CREATE TYPE public.{name}" not in sql
    ]
    assert not missing, (
        f"these enum types are never created for Postgres: {missing}. "
        f"An enum column added as VARCHAR will fail at runtime with "
        f'\'type "..." does not exist\'.'
    )


def test_the_tournament_kind_column_is_an_enum_on_postgres():
    """The specific regression, pinned."""
    sql = _postgres_migration_sql()
    assert "CREATE TYPE tournamentkind" in sql
    assert "TYPE tournamentkind" in sql

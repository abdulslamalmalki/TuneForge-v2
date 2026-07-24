from alembic import context
from alembic.util.exc import CommandError
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app import config as app_config
from app.db.models import Base
from app.db.sqlite_storage import SQLiteStorageError, verify_sqlite_storage

target_metadata = Base.metadata


def _command_writes_database() -> bool:
    """Return whether the active Alembic command can mutate schema state."""

    options = context.config.cmd_opts
    if options is None:
        # Programmatic Alembic callers do not provide CLI options. TuneForge
        # currently uses this path for upgrade tests, so retain the safe gate.
        return True
    command = getattr(options, "cmd", None)
    function = command[0] if command else None
    return getattr(function, "__name__", "") in {
        "upgrade",
        "downgrade",
        "stamp",
        "ensure_version",
    }


def run_migrations_offline() -> None:
    context.configure(
        url=app_config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    app_config.ensure_data_dirs()
    if _command_writes_database():
        try:
            verify_sqlite_storage(app_config.DATABASE_PATH)
        except SQLiteStorageError as error:
            raise CommandError(str(error)) from error
    # Alembic is a short-lived process. NullPool plus explicit disposal ensures
    # Windows releases the database handle before callers inspect, move, or
    # remove migrated test/staging databases.
    engine = create_engine(app_config.DATABASE_URL, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

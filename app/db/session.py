from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import config

# check_same_thread=False: FastAPI runs sync routes in a threadpool, so pooled
# connections cross threads; SQLAlchemy serializes access per checkout.
engine = create_engine(
    config.DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": config.SQLITE_BUSY_TIMEOUT_SECONDS,
    },
)


@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _connection_record) -> None:
    # Keep the single-instance SQLite deployment responsive during short writer
    # contention while preserving foreign keys and durable commits. WAL lets
    # public reads continue during curator writes; it persists on the database.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
    actual_mode = cursor.execute(
        f"PRAGMA journal_mode={config.SQLITE_JOURNAL_MODE}"
    ).fetchone()[0]
    if str(actual_mode).lower() != config.SQLITE_JOURNAL_MODE:
        cursor.close()
        raise RuntimeError(
            "SQLite could not activate the configured journal mode: "
            f"expected {config.SQLITE_JOURNAL_MODE}, received {actual_mode}."
        )
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

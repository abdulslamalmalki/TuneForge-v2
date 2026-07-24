from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import config

# check_same_thread=False: FastAPI runs sync routes in a threadpool, so pooled
# connections cross threads; SQLAlchemy serializes access per checkout.
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _connection_record) -> None:
    # SQLite ships with foreign-key enforcement off; NFR-2 requires it on.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

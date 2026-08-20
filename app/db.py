"""SQLite engine + session. Single file, no server, Railway-friendly."""

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = os.environ.get("CGJB_DB", "careergap.db")
ENGINE_URL = f"sqlite+pysqlite:///{DB_PATH}"

engine = create_engine(ENGINE_URL, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")   # concurrent reads while you curate
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)

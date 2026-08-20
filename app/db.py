"""SQLite engine + session.

Database location, in order of precedence:

1. `CGJB_DB` - set this explicitly in production.
2. On Railway with no `CGJB_DB`, `/data/careergap.db`, the conventional volume
   mount point.
3. Locally, `careergap.db` in the working directory.

The Railway default is a safety net, not a substitute for a volume. Railway's
container filesystem is ephemeral: without a Volume mounted at /data, this file
is destroyed on every redeploy and every restart, taking the whole board with
it. `storage_is_ephemeral()` reports that, and /health surfaces it.
"""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)

ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
RAILWAY_VOLUME = Path("/data")


def _resolve_db_path() -> Path:
    explicit = os.environ.get("CGJB_DB")
    if explicit:
        return Path(explicit)
    if ON_RAILWAY:
        return RAILWAY_VOLUME / "careergap.db"
    return Path("careergap.db")


DB_PATH = _resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ENGINE_URL = f"sqlite+pysqlite:///{DB_PATH}"


def storage_is_ephemeral() -> bool:
    """True when the database is sitting on disk that a redeploy will wipe.

    Detected by checking whether the parent directory is its own mount point -
    a Railway Volume is, the container's own filesystem is not.
    """
    if not ON_RAILWAY:
        return False
    parent = DB_PATH.parent
    try:
        return not parent.is_mount()
    except OSError:
        return True


if storage_is_ephemeral():
    log.warning(
        "%s is on ephemeral container storage - every redeploy will destroy it. "
        "Mount a Railway Volume at %s, or set CGJB_DB to a path inside one.",
        DB_PATH,
        RAILWAY_VOLUME,
    )

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
    """Dev and test convenience. Production schema comes from Alembic."""
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)

import asyncio

from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from alembic import command
from config import get_settings

_engine = None


def get_engine():
    global _engine
    if not _engine:
        _engine = create_engine(
            f"sqlite:///{get_settings().SQLITE_FILE}",
            connect_args={"check_same_thread": False},
        )
    return _engine


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def init_and_migrate_db():
    alembic_cfg = Config("alembic.ini")
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

"""SQLite engines shared by app state, workspaces, and schema setup."""

from pathlib import Path

import sqlite_vec
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_database_engine(url: str) -> AsyncEngine:
    database = make_url(url).database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_async_engine(url, connect_args={"timeout": 10}, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(connection, _record):
        async def load_vector_extension(raw):
            await raw.enable_load_extension(True)
            try:
                await raw.load_extension(sqlite_vec.loadable_path())
            finally:
                await raw.enable_load_extension(False)

        connection.run_async(load_vector_extension)
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine

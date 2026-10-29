from contextlib import contextmanager

import psycopg2

from backend.core.config import Settings, get_settings


def connect(settings: Settings | None = None):
    config = settings or get_settings()
    return psycopg2.connect(
        dbname=config.db_name,
        host=config.db_host,
        port=config.db_port,
        user=config.db_user,
        password=config.db_password,
        connect_timeout=config.db_connect_timeout,
        options=f"-c statement_timeout={config.db_statement_timeout_ms}",
    )


@contextmanager
def connection(settings: Settings | None = None):
    """Commit on success, roll back on error, and always close the connection."""
    conn = connect(settings)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

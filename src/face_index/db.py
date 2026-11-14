"""Opening a database connection with finite timeouts, shared by both Postgres adapters."""

import psycopg

from face_index import config


def connect(dsn: str) -> psycopg.Connection:
    """An autocommit connection that fails fast when the server is unreachable or stuck."""
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=config.DB_CONNECT_TIMEOUT)
    # Set after connecting, so it does not replace any ``options`` the DSN already carries.
    conn.execute(
        "SELECT set_config('statement_timeout', %s, false)", (str(config.DB_STATEMENT_TIMEOUT_MS),)
    )
    return conn

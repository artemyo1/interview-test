from unittest.mock import Mock, sentinel

from etl.db import get_connection


def test_get_connection_passes_settings(monkeypatch):
    db_config = {
        "host": "localhost",
        "port": 5432,
        "dbname": "test_database",
        "user": "test_db_user",
        "password": "test_password",
    }

    connect = Mock(return_value=sentinel.connection)
    monkeypatch.setattr("etl.db.psycopg.connect", connect)

    connection = get_connection(db_config)

    connect.assert_called_once_with(
        **db_config,
        connect_timeout=10,
    )
    assert connection is sentinel.connection

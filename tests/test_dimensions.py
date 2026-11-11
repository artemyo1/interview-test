from unittest.mock import MagicMock, Mock

from etl import load


def test_refresh_dimensions(monkeypatch):
    config = {"db": {"dbname": "test_database"}}

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (40,)

    connect = Mock(return_value=connection)
    monkeypatch.setattr(load, "get_connection", connect)

    rows = load.refresh_dimensions(config)

    connect.assert_called_once_with(config["db"])
    assert rows == 40

    statements = [item.args[0] for item in connection.execute.call_args_list]

    assert any(
        "CREATE TABLE IF NOT EXISTS core.dim_cities" in sql for sql in statements
    )
    assert any("INSERT INTO core.dim_cities" in sql for sql in statements)

    connection.__exit__.assert_called_once_with(None, None, None)

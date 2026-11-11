from unittest.mock import Mock

from etl import bootstrap


def test_initialize_source_passes_parameters(monkeypatch):
    config = {
        "db": {
            "host": "postgres",
            "port": 5432,
            "dbname": "test_database",
            "user": "test_db_user",
            "password": "test_password",
        },
        "n_prices": 300000,
    }

    run = Mock()
    monkeypatch.setattr("subprocess.run", run)

    bootstrap.initialize_source(config)

    run.assert_called_once()

    args, kwargs = run.call_args
    command = args[0]

    assert command[0] == "psql"
    assert "n_prices=300000" in command
    assert command[-1].endswith("sql/000_init_raw.sql")
    assert "test_password" not in command

    assert kwargs["check"] is True
    assert kwargs["env"]["PGHOST"] == "postgres"
    assert kwargs["env"]["PGDATABASE"] == "test_database"
    assert kwargs["env"]["PGUSER"] == "test_db_user"
    assert kwargs["env"]["PGPASSWORD"] == "test_password"

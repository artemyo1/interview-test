import sys
from datetime import date

import pytest

import main as cli


def test_check_config(monkeypatch, capsys):
    monkeypatch.setenv("DB_NAME", "test_database")
    monkeypatch.setenv("DB_USER", "test_db_user")
    monkeypatch.setenv("DB_PASSWORD", "test_password")

    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "check-config"],
    )

    cli.main()

    output = capsys.readouterr().out

    assert "Конфигурация корректна" in output
    assert "test_password" not in output


def test_load_core_command_passes_dates(monkeypatch):
    calls = []
    config = {"db": {"dbname": "test_database"}}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        cli, "load_core", lambda *args: calls.append(args), raising=False
    )
    monkeypatch.setattr(sys, "argv", [
        "main.py", "load-core",
        "--start-date", "2026-10-01",
        "--end-date", "2026-10-26",
    ])

    cli.main()

    assert calls == [(config, date(2026, 10, 1), date(2026, 10, 26))]


@pytest.mark.parametrize("start_date,end_date", [
    ("2026-02-30", "2026-10-26"),
    ("2026-10-27", "2026-10-26"),
])
def test_load_core_command_rejects_invalid_dates(monkeypatch, start_date, end_date):
    def unexpected_config():
        raise AssertionError("Проверка дат должна выполняться до чтения настроек")

    monkeypatch.setattr(cli, "load_config", unexpected_config)
    monkeypatch.setattr(sys, "argv", [
        "main.py", "load-core",
        "--start-date", start_date,
        "--end-date", end_date,
    ])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2


@pytest.mark.parametrize("arguments,expected_date", [
    ([], date(2026, 10, 26)),
    (["--as-of", "2026-09-30"], date(2026, 9, 30)),
])
def test_refresh_mart_command_passes_snapshot_date(monkeypatch, arguments, expected_date):
    calls = []
    config = {"db": {"dbname": "test_database"}, "as_of": date(2026, 10, 26)}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        cli, "refresh_market_corridor", lambda *args: calls.append(args), raising=False
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "refresh-mart", *arguments])

    cli.main()

    assert calls == [(config, expected_date)]


def test_refresh_mart_command_reports_failed_rebuild(monkeypatch, capsys):
    config = {"db": {"dbname": "test_database"}, "as_of": date(2026, 10, 26)}

    def failed_refresh(*args):
        raise RuntimeError("Ошибка расчёта")

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "refresh_market_corridor", failed_refresh, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "refresh-mart"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    assert '"status": "failed"' in capsys.readouterr().out


@pytest.mark.parametrize("arguments,expected_date", [
    ([], date(2026, 10, 26)),
    (["--as-of", "2026-09-30"], date(2026, 9, 30)),
])
def test_train_ml_command_passes_snapshot_date(monkeypatch, arguments, expected_date):
    calls = []
    config = {"db": {"dbname": "test_database"}, "as_of": date(2026, 10, 26)}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "run_ml", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "train-ml", *arguments])

    cli.main()

    assert calls == [(config, expected_date)]


def test_train_ml_command_reports_failed_training(monkeypatch, capsys):
    config = {"db": {"dbname": "test_database"}, "as_of": date(2026, 10, 26)}

    def failed_training(*args):
        raise ValueError("Недостаточно данных")

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "run_ml", failed_training, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "train-ml"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    assert '"status": "failed"' in capsys.readouterr().out


@pytest.mark.parametrize("arguments,expected_date", [
    ([], date(2026, 10, 26)),
    (["--as-of", "2026-09-30"], date(2026, 9, 30)),
])
def test_run_command_starts_whole_pipeline(monkeypatch, arguments, expected_date):
    calls = []
    config = {"db": {}, "as_of": date(2026, 10, 26)}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "run_pipeline", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "run", *arguments])

    cli.main()

    assert calls == [(config, expected_date)]


def test_run_command_reports_failed_pipeline(monkeypatch, capsys):
    config = {"db": {}, "as_of": date(2026, 10, 26)}

    def failed_pipeline(*args):
        raise RuntimeError("Ошибка этапа")

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "run_pipeline", failed_pipeline, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "run"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    assert '"status": "failed"' in capsys.readouterr().out

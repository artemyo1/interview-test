import sys

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

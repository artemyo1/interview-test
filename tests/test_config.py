from datetime import date
from pathlib import Path

import pytest

from etl.config import load_config


@pytest.fixture(autouse=True)
def config_env(monkeypatch):
    variables = (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "AS_OF",
        "N_PRICES",
        "ML_SEED",
        "MODEL_DIR",
    )

    for name in variables:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("DB_NAME", "test_database")
    monkeypatch.setenv("DB_USER", "test_db_user")
    monkeypatch.setenv("DB_PASSWORD", "test_password")


def test_db_user_comes_from_db_user(monkeypatch):
    monkeypatch.setenv("DB_NAME", "test_database")
    monkeypatch.setenv("DB_USER", "test_db_user")
    monkeypatch.setenv("DB_PASSWORD", "test_password")

    monkeypatch.setenv("USER", "system_user")

    config = load_config()

    assert config["db"]["user"] == "test_db_user"


def test_config_defaults():
    config = load_config()

    assert config["db"]["host"] == "localhost"
    assert config["db"]["port"] == 5432
    assert config["as_of"] == date(2026, 10, 26)
    assert config["n_prices"] == 300000
    assert config["ml_seed"] == 42
    assert config["model_dir"] == Path("artifacts")


@pytest.mark.parametrize(
    "variable",
    ["DB_NAME", "DB_USER", "DB_PASSWORD"],
)
def test_missing_required_variable(monkeypatch, variable):
    monkeypatch.delenv(variable)

    with pytest.raises(ValueError, match=variable):
        load_config()

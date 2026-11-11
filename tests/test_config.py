import json
import os
import shutil
import subprocess
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


@pytest.mark.parametrize("overrides", [
    {},
    {
        "DB_NAME": "custom_database",
        "DB_USER": "custom_user",
        "DB_PASSWORD": "example_override",
        "AS_OF": "2026-09-30",
        "N_PRICES": "10000",
        "ML_SEED": "7",
    },
])
def test_compose_runs_without_env_file_and_accepts_overrides(overrides):
    if shutil.which("docker") is None:
        pytest.skip("Проверка Compose выполняется на машине с Docker CLI")
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    for name in (
        "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
        "AS_OF", "N_PRICES", "ML_SEED", "COMPOSE_FILE", "COMPOSE_ENV_FILES",
    ):
        environment.pop(name, None)
    environment.update(overrides)
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", os.devnull,
            "-f", str(root / "docker-compose.yml"), "config", "--format", "json",
        ],
        env=environment, capture_output=True, text=True, check=True,
    )
    services = json.loads(result.stdout)["services"]
    postgres = services["postgres"]["environment"]
    pipeline = services["pipeline"]["environment"]
    assert postgres["POSTGRES_DB"] == pipeline["DB_NAME"] == overrides.get("DB_NAME", "deals_bigdata")
    assert postgres["POSTGRES_USER"] == pipeline["DB_USER"] == overrides.get("DB_USER", "logistics")
    assert postgres["POSTGRES_PASSWORD"] == pipeline["DB_PASSWORD"] == overrides.get("DB_PASSWORD", "logistics")
    assert pipeline["DB_HOST"] == "postgres"
    assert pipeline["DB_PORT"] == "5432"
    assert pipeline["AS_OF"] == overrides.get("AS_OF", "2026-10-26")
    assert pipeline["N_PRICES"] == overrides.get("N_PRICES", "300000")
    assert pipeline["ML_SEED"] == overrides.get("ML_SEED", "42")
    if not overrides:
        example = {}
        for line in (root / ".env.example").read_text().splitlines():
            if line and not line.startswith("#"):
                name, value = line.split("=", 1)
                example[name] = value
        for name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "AS_OF", "N_PRICES", "ML_SEED"):
            assert example[name] == pipeline[name], name
    assert 'CMD ["python", "main.py", "run"]' in (root / "docker/Dockerfile").read_text()
    assert (root / ".dockerignore").read_text().splitlines()[0] == "**"

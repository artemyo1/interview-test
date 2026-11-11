import os
from datetime import date
from pathlib import Path


def load_config():
    required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Не заданы переменные окружения {', '.join(missing)}")

    db_port = int(os.getenv("DB_PORT", "5432"))
    n_prices = int(os.getenv("N_PRICES", "300000"))

    if not 1 <= db_port <= 65535:
        raise ValueError("DB_PORT должен быть от 1 до 65535")

    if n_prices <= 0:
        raise ValueError("N_PRICES должно быть положительным")

    return {
        "db": {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": db_port,
            "dbname": os.environ["DB_NAME"],
            "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"],
        },
        "as_of": date.fromisoformat(os.getenv("AS_OF", "2026-10-26")),
        "n_prices": n_prices,
        "ml_seed": int(os.getenv("ML_SEED", "42")),
        "model_dir": Path(os.getenv("MODEL_DIR", "artifacts")),
    }

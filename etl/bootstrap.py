import logging
import os
import subprocess
from pathlib import Path
from time import perf_counter


def initialize_source(config):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()

    db = config["db"]
    script = Path(__file__).resolve().parents[1] / "sql" / "000_init_raw.sql"

    environment = os.environ.copy()
    environment.update(
        {
            "PGHOST": db["host"],
            "PGPORT": str(db["port"]),
            "PGDATABASE": db["dbname"],
            "PGUSER": db["user"],
            "PGPASSWORD": db["password"],
            "PGCONNECT_TIMEOUT": "10",
        }
    )

    logger.info(
        "Начало инициализации источника",
        extra={
            "context": {
                "step": "init",
                "status": "started",
                "n_prices": config["n_prices"],
            }
        },
    )

    subprocess.run(
        [
            "psql",
            "-X",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"n_prices={config['n_prices']}",
            "-f",
            str(script),
        ],
        env=environment,
        check=True,
    )

    logger.info(
        "Инициализация источника завершена",
        extra={
            "context": {
                "step": "init",
                "status": "success",
                "duration_seconds": round(
                    perf_counter() - started_at,
                    3,
                ),
            }
        },
    )

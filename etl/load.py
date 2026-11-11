import logging
from pathlib import Path
from time import perf_counter
from typing import LiteralString, cast

from etl.db import get_connection


def refresh_dimensions(config):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()

    sql_dir = Path(__file__).resolve().parents[1] / "sql"

    schema_sql = (sql_dir / "001_core.sql").read_text(encoding="utf-8")
    dimensions_sql = (sql_dir / "010_dimensions.sql").read_text(encoding="utf-8")

    with get_connection(config["db"]) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(20261026)")

        connection.execute(cast(LiteralString, schema_sql))
        connection.execute(cast(LiteralString, dimensions_sql))

        rows = connection.execute("SELECT count(*) FROM core.dim_cities").fetchone()
        if rows is None:
            raise ValueError("Запрос количества городов не вернул строку")

        rows = rows[0]

    logger.info(
        "Справочники обновлены",
        extra={
            "context": {
                "step": "dimensions",
                "status": "success",
                "cities_count": rows,
                "duration_seconds": round(
                    perf_counter() - started_at,
                    3,
                ),
            }
        },
    )
    return rows

import logging
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import LiteralString, cast

from etl.db import get_connection


def _refresh_dimensions(connection):
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    schema_sql = (sql_dir / "001_core.sql").read_text(encoding="utf-8")
    dimensions_sql = (sql_dir / "010_dimensions.sql").read_text(encoding="utf-8")

    connection.execute("SELECT pg_advisory_xact_lock(20261026)")
    connection.execute(cast(LiteralString, schema_sql))
    connection.execute(cast(LiteralString, dimensions_sql))


def refresh_dimensions(config):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()

    with get_connection(config["db"]) as connection:
        _refresh_dimensions(connection)

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


def load_core(config, start_date: date, end_date: date):
    if start_date > end_date:
        raise ValueError("start_date должна быть не позже end_date")

    logger = logging.getLogger("logistics")
    started_at = perf_counter()
    period = {"start_date": start_date, "end_date": end_date}
    context = {
        "step": "load_core",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    logger.info(
        "Начало загрузки Core",
        extra={"context": {**context, "status": "started"}},
    )

    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    load_sql = (sql_dir / "020_load_core.sql").read_text(encoding="utf-8")

    with get_connection(config["db"]) as connection:
        _refresh_dimensions(connection)

        counts = dict(connection.execute(
            """
            SELECT n.dq_reason, count(*)
            FROM raw.prices AS p
            JOIN core.price_normalization AS n ON n.price_id = p.id
            WHERE p.loading_date BETWEEN %(start_date)s AND %(end_date)s
            GROUP BY n.dq_reason
            """,
            period,
        ).fetchall())
        expected_count = counts.pop(None, 0)
        rejected_count = sum(counts.values())

        deleted_count = connection.execute(
            """
            DELETE FROM core.fact_prices
            WHERE loading_date BETWEEN %(start_date)s AND %(end_date)s
               OR price_id IN (
                    SELECT id FROM raw.prices
                    WHERE loading_date BETWEEN %(start_date)s AND %(end_date)s
               )
            """,
            period,
        ).rowcount

        loaded_count = connection.execute(
            cast(LiteralString, load_sql), period
        ).rowcount
        if loaded_count != expected_count:
            raise RuntimeError("Количество загруженных сделок не совпало с ожидаемым")

        body_groups_count = connection.execute(
            """
            INSERT INTO core.fact_price_body_groups (price_id, body_group)
            SELECT b.price_id, b.body_group
            FROM core.price_body_groups AS b
            JOIN core.fact_prices AS p ON p.price_id = b.price_id
            WHERE p.loading_date BETWEEN %(start_date)s AND %(end_date)s
            """,
            period,
        ).rowcount

        price_dates_count = connection.execute(
            """
            INSERT INTO core.fact_price_dates (price_id, price_date)
            SELECT d.price_id, d.price_date
            FROM raw.price_dates AS d
            JOIN core.fact_prices AS p ON p.price_id = d.price_id
            WHERE p.loading_date BETWEEN %(start_date)s AND %(end_date)s
            """,
            period,
        ).rowcount

    result = {
        "read_count": expected_count + rejected_count,
        "loaded_count": loaded_count,
        "rejected_count": rejected_count,
        "dq_reasons": counts,
        "deleted_count": deleted_count,
        "body_groups_count": body_groups_count,
        "price_dates_count": price_dates_count,
    }
    logger.info(
        "Загрузка Core завершена",
        extra={
            "context": {
                **context,
                **result,
                "status": "success",
                "duration_seconds": round(perf_counter() - started_at, 3),
            }
        },
    )
    return result


def refresh_market_corridor(config, as_of: date):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()
    context = {"step": "market_corridor", "as_of": as_of.isoformat()}
    logger.info(
        "Начало расчёта рыночной витрины",
        extra={"context": {**context, "status": "started"}},
    )

    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    schema_sql = (sql_dir / "002_mart.sql").read_text(encoding="utf-8")
    market_sql = (sql_dir / "030_market_corridor.sql").read_text(encoding="utf-8")

    with get_connection(config["db"]) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(20261026)")
        connection.execute(cast(LiteralString, schema_sql))
        connection.execute("DELETE FROM mart.market_corridor")
        rows = connection.execute(
            cast(LiteralString, market_sql), {"as_of": as_of}
        ).rowcount

    logger.info(
        "Рыночная витрина обновлена",
        extra={
            "context": {
                **context,
                "status": "success",
                "rows_count": rows,
                "duration_seconds": round(perf_counter() - started_at, 3),
            }
        },
    )
    return rows

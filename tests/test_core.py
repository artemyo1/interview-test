import os
from datetime import date
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation
from psycopg import sql

from etl import load
from etl.config import load_config
from etl.db import get_connection


def test_load_core_rejects_reversed_window():
    load_core = getattr(load, "load_core", None)
    assert callable(load_core), "Нет функции загрузки Core"

    with pytest.raises(ValueError, match="start_date"):
        load_core({}, date(2026, 10, 2), date(2026, 10, 1))


@pytest.fixture
def core_config():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Для проверки PostgreSQL задай RUN_DB_TESTS=1")

    config = load_config()
    database = "core_test_" + uuid4().hex
    admin_db = config["db"].copy()
    config["db"] = {**admin_db, "dbname": database}

    with get_connection(admin_db) as connection:
        connection.autocommit = True
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    try:
        with get_connection(config["db"]) as connection:
            connection.execute("CREATE SCHEMA raw; SET search_path TO raw, public")
            schema = Path("sql/schema.sql").read_text(encoding="utf-8")
            connection.execute(cast(LiteralString, schema))
            connection.execute("""
                INSERT INTO raw.cities (gc_id, name, full_name_local, full_name_gc)
                VALUES
                    (1001, 'Москва', 'Москва, г. Москва', 'Москва'),
                    (1003, 'Казань', 'Казань, Татарстан', 'Казань');
                INSERT INTO raw.body_types (id, name, "id_from_1C", std_id)
                VALUES (56, 'тентованный', 56, 56),
                       (57, 'тент бок', 57, 56),
                       (15, 'борт', 15, 15);
                INSERT INTO raw.exchange_rates (num_code, value, setting_date)
                VALUES ('840', 90, '2026-09-30');
            """)
        yield config
    finally:
        with get_connection(admin_db) as connection:
            connection.autocommit = True
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def insert_price(connection, price_id, loading_date, **changes):
    values = {
        "id": price_id,
        "loading_date": loading_date,
        "loading_city_gcid": 1001,
        "unloading_city_gcid": 1003,
        "distance": 800,
        "total_weight": 20,
        "total_volume": 90,
        "body_type": 56,
        "price": 10000,
        "currency_code": "643",
        "nearest_rate_date": date(2026, 9, 30),
        "weight_category": None,
        "volume_category": None,
        **changes,
    }
    connection.execute(
        sql.SQL("INSERT INTO raw.prices ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        list(values.values()),
    )


def test_load_core_saves_period_and_related_data(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
        insert_price(connection, 2, date(2026, 10, 2))
        connection.execute("""
            INSERT INTO raw.price_body_types (price_id, body_type_id)
            VALUES (1, 56), (1, 57), (1, 15);
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01'), (1, '2026-11-01');
        """)

    result = load.load_core(core_config, day, day)

    assert result["read_count"] == 1
    assert result["loaded_count"] == 1
    assert result["rejected_count"] == 0
    assert result["body_groups_count"] == 2
    assert result["price_dates_count"] == 2
    with get_connection(core_config["db"]) as connection:
        assert connection.execute("""
            SELECT price_id, price_rub, weight_category, volume_category
            FROM core.fact_prices
        """).fetchall() == [(1, 10000, "FTL", "FTL")]
        assert connection.execute("""
            SELECT body_group FROM core.fact_price_body_groups ORDER BY body_group
        """).fetchall() == [("Борт",), ("Тент",)]
        assert connection.execute(
            "SELECT count(*) FROM core.fact_price_dates"
        ).fetchone() == (2,)


def read_core(config):
    with get_connection(config["db"]) as connection:
        return {
            table: connection.execute(
                sql.SQL("SELECT * FROM core.{} ORDER BY 1, 2").format(
                    sql.Identifier(table)
                )
            ).fetchall()
            for table in (
                "fact_prices", "fact_price_body_groups", "fact_price_dates"
            )
        }


def test_load_core_repeat_keeps_all_values(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
        insert_price(connection, 2, date(2026, 10, 2), body_type=None)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01'), (2, '2026-10-02');
        """)

    first = load.load_core(core_config, day, date(2026, 10, 2))
    before = read_core(core_config)
    second = load.load_core(core_config, day, date(2026, 10, 2))

    assert read_core(core_config) == before
    assert first["loaded_count"] == second["loaded_count"] == 2
    assert first["deleted_count"] == 0
    assert second["deleted_count"] == 2
    assert (2, "Не указан") in before["fact_price_body_groups"]


def test_load_core_updates_period_and_removes_rejected_rows(core_config):
    day = date(2026, 10, 1)
    next_day = date(2026, 10, 2)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
        insert_price(connection, 2, day)
        insert_price(connection, 3, next_day)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01'), (2, '2026-10-01');
        """)
    load.load_core(core_config, day, next_day)

    with get_connection(core_config["db"]) as connection:
        connection.execute("UPDATE raw.prices SET price = 0 WHERE id = 1")
        connection.execute("DELETE FROM raw.prices WHERE id = 2")
        connection.execute("UPDATE raw.prices SET price = 20000 WHERE id = 3")

    result = load.load_core(core_config, day, day)

    assert result["read_count"] == result["rejected_count"] == 1
    assert result["loaded_count"] == 0
    assert result["dq_reasons"] == {"NON_POSITIVE_PRICE": 1}
    with get_connection(core_config["db"]) as connection:
        assert connection.execute(
            "SELECT price_id, price_rub FROM core.fact_prices"
        ).fetchall() == [(3, 10000)]
        assert connection.execute(
            "SELECT count(*) FROM core.fact_price_dates"
        ).fetchone() == (0,)


@pytest.mark.parametrize("gc_id,reason", [
    (1001, "UNKNOWN_LOADING_CITY"),
    (1003, "UNKNOWN_UNLOADING_CITY"),
])
def test_load_core_rejects_city_removed_from_raw(core_config, gc_id, reason):
    day = date(2026, 10, 1)
    next_day = date(2026, 10, 2)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
        insert_price(connection, 2, next_day)
        connection.execute("""
            INSERT INTO raw.cities (gc_id, name, full_name_local, full_name_gc)
            VALUES
                (1001, 'Москва', 'Москва, г. Москва', 'Москва'),
                (1003, 'Казань', 'Казань, Татарстан', 'Казань');
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01'), (2, '2026-10-02');
        """)

    first = load.load_core(core_config, day, next_day)

    assert first["read_count"] == first["loaded_count"] == 2
    before = read_core(core_config)
    with get_connection(core_config["db"]) as connection:
        cities_before = connection.execute(
            "SELECT * FROM core.dim_cities ORDER BY gc_id"
        ).fetchall()
        connection.execute("DELETE FROM raw.cities WHERE gc_id = %s", (gc_id,))

    result = load.load_core(core_config, day, day)

    assert result["loaded_count"] == 0
    assert result["read_count"] == result["rejected_count"] == 1
    assert result["dq_reasons"] == {reason: 1}
    assert result["deleted_count"] == 1
    assert read_core(core_config) == {
        table: [row for row in rows if row[0] == 2]
        for table, rows in before.items()
    }
    with get_connection(core_config["db"]) as connection:
        assert connection.execute(
            "SELECT * FROM core.dim_cities ORDER BY gc_id"
        ).fetchall() == cities_before


def test_load_core_handles_changed_loading_date(core_config):
    day = date(2026, 10, 1)
    next_day = date(2026, 10, 2)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
    load.load_core(core_config, day, day)
    with get_connection(core_config["db"]) as connection:
        connection.execute(
            "UPDATE raw.prices SET loading_date = %s WHERE id = 1", (next_day,)
        )

    load.load_core(core_config, next_day, next_day)

    with get_connection(core_config["db"]) as connection:
        assert connection.execute(
            "SELECT price_id, loading_date FROM core.fact_prices"
        ).fetchall() == [(1, next_day)]


def test_load_core_rolls_back_when_related_insert_fails(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01');
        """)
    load.load_core(core_config, day, day)
    before = read_core(core_config)

    with get_connection(core_config["db"]) as connection:
        connection.execute("""
            ALTER TABLE core.fact_price_dates
                ADD CONSTRAINT reject_november
                CHECK (price_date <> DATE '2026-11-01');
            UPDATE raw.prices SET price = 20000 WHERE id = 1;
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-11-01');
        """)

    with pytest.raises(CheckViolation):
        load.load_core(core_config, day, day)

    assert read_core(core_config) == before


def test_load_core_logs_dq_counts_and_uses_exact_currency_date(core_config):
    day = date(2026, 10, 1)
    rejected = [
        ({"price": 0}, "NON_POSITIVE_PRICE"),
        ({"price": 1000}, "RUB_PRICE_TOO_LOW"),
        ({"distance": 0}, "NON_POSITIVE_DISTANCE"),
        ({"total_weight": -1}, "NEGATIVE_WEIGHT"),
        ({"total_volume": -1}, "NEGATIVE_VOLUME"),
        ({"loading_city_gcid": 9999}, "UNKNOWN_LOADING_CITY"),
        ({"unloading_city_gcid": 9999}, "UNKNOWN_UNLOADING_CITY"),
        ({"currency_code": "999"}, "UNSUPPORTED_CURRENCY"),
        ({"currency_code": "840", "nearest_rate_date": None}, "MISSING_EXCHANGE_RATE"),
        ({"currency_code": "978"}, "MISSING_EXCHANGE_RATE"),
        ({"currency_code": "840", "nearest_rate_date": date(2026, 9, 29)},
         "INVALID_EXCHANGE_RATE"),
    ]
    with get_connection(core_config["db"]) as connection:
        connection.execute("""
            INSERT INTO raw.exchange_rates (num_code, value, setting_date)
            VALUES ('978', 100, '2026-09-29'),
                   ('840', 0, '2026-09-29'),
                   ('840', 999, '2026-09-30');
        """)
        insert_price(connection, 1, day, price=200, currency_code="840")
        insert_price(connection, 2, day, nearest_rate_date=None)
        insert_price(connection, 3, day, is_fact=False, complex=True, outlier_mark=True)
        for price_id, (changes, _) in enumerate(rejected, start=10):
            insert_price(connection, price_id, day, **changes)

    result = load.load_core(core_config, day, day)

    expected_reasons = {}
    for _, reason in rejected:
        expected_reasons[reason] = expected_reasons.get(reason, 0) + 1
    assert result["dq_reasons"] == expected_reasons
    assert result["rejected_count"] == len(rejected)
    assert result["read_count"] == len(rejected) + 3
    assert result["loaded_count"] == 3
    with get_connection(core_config["db"]) as connection:
        assert connection.execute("""
            SELECT price_id, price_rub FROM core.fact_prices ORDER BY price_id
        """).fetchall() == [(1, 200 * 90), (2, 10000), (3, 10000)]


@pytest.mark.parametrize("weight,volume,weight_category,volume_category", [
    (30, 120, "G20T", "FTL"),
    (10, 0, "FTL", "OTH"),
    (5, 121, "10T", "OTH"),
    (2.5, 1, "5T", "FTL"),
    (0.5, 1, "L5T", "FTL"),
    (0, 1, "OTH", "FTL"),
    (9.96, 1, "FTL", "FTL"),
])
def test_load_core_fills_missing_categories(
    core_config, weight, volume, weight_category, volume_category
):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day, total_weight=weight, total_volume=volume)

    load.load_core(core_config, day, day)

    with get_connection(core_config["db"]) as connection:
        assert connection.execute("""
            SELECT weight_category, volume_category FROM core.fact_prices
        """).fetchone() == (weight_category, volume_category)


def test_load_core_preserves_categories_and_future_dates(core_config):
    day = date(2026, 11, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(
            connection, 1, day, weight_category="OTH", volume_category="OTH"
        )
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-11-01'), (1, '2026-12-01');
        """)

    load.load_core(core_config, day, day)

    with get_connection(core_config["db"]) as connection:
        assert connection.execute("""
            SELECT weight_category, volume_category FROM core.fact_prices
        """).fetchone() == ("OTH", "OTH")
        assert connection.execute(
            "SELECT count(*) FROM core.fact_price_dates"
        ).fetchone() == (2,)


def test_load_core_empty_period(core_config):
    day = date(2026, 10, 1)

    result = load.load_core(core_config, day, day)

    assert result["read_count"] == result["loaded_count"] == 0
    assert result["rejected_count"] == 0
    assert result["dq_reasons"] == {}
    assert read_core(core_config) == {
        "fact_prices": [], "fact_price_body_groups": [], "fact_price_dates": []
    }


def test_load_core_uses_primary_body_only_without_links(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day, body_type=56)
        insert_price(connection, 2, day, body_type=56)
        connection.execute("""
            INSERT INTO raw.price_body_types (price_id, body_type_id)
            VALUES (1, 15);
        """)

    load.load_core(core_config, day, day)

    with get_connection(core_config["db"]) as connection:
        assert connection.execute("""
            SELECT price_id, body_group FROM core.fact_price_body_groups
            ORDER BY price_id, body_group
        """).fetchall() == [(1, "Борт"), (2, "Тент")]

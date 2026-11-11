from datetime import date, timedelta
from statistics import mean, quantiles, stdev

import pytest
from psycopg.rows import dict_row
from psycopg.errors import CheckViolation

from etl import load
from etl.db import get_connection
from test_core import core_config, insert_price


def test_market_corridor_calculates_prices_without_duplicate_deals(core_config):
    refresh = getattr(load, "refresh_market_corridor", None)
    assert callable(refresh), "Нет функции расчёта рыночной витрины"

    prices = [2000, 4000, 8000, 16000]
    distances = [100, 100, 200, 400]
    price_types = [3, 9, 1, 3]
    organizations = [11, 12, 13, 11]
    with get_connection(core_config["db"]) as connection:
        connection.execute("""
            INSERT INTO raw.cities (gc_id, name, full_name_local, full_name_gc)
            VALUES (1001, 'Москва', 'Москва дубль, другой регион', 'Москва');
        """)
        for index, price in enumerate(prices):
            price_id = index + 1
            insert_price(
                connection, price_id,
                date(2026, 9, 20) if index == 0 else date(2026, 10, 1),
                price=price, distance=distances[index],
                price_type=price_types[index], organization_id=organizations[index],
                total_volume=130 if index == 0 else 90,
            )
            connection.execute(
                "INSERT INTO raw.price_dates (price_id, price_date) VALUES (%s, %s)",
                (price_id, date(2026, 10, 10)),
            )
        connection.execute("""
            INSERT INTO raw.price_body_types (price_id, body_type_id)
            VALUES (1, 56), (1, 57);
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01');
        """)
    load.load_core(core_config, date(2026, 9, 20), date(2026, 10, 1))

    assert refresh(core_config, date(2026, 10, 26)) == 1

    with get_connection(core_config["db"]) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute("SELECT * FROM mart.route_card").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["price_month"] == date(2026, 10, 1)
    assert row["loading_city_name"] == "Москва"
    assert row["unloading_city_name"] == "Казань"
    assert row["body_group"] == "Тент"
    assert row["weight_category"] == "FTL"
    assert row["trips_count"] == 4
    assert row["carriers_count"] == 2
    assert [row["p25_trip"], row["median_trip"], row["p75_trip"]] == pytest.approx(
        quantiles(prices, n=4, method="inclusive")
    )
    km_prices = [price / distance for price, distance in zip(prices, distances)]
    assert [row["p25_km"], row["median_km"], row["p75_km"]] == pytest.approx(
        quantiles(km_prices, n=4, method="inclusive")
    )
    assert row["avg_trip"] == pytest.approx(mean(prices))
    assert row["cv_rate"] == pytest.approx(stdev(prices) / mean(prices))
    assert row["mom_pct"] is None
    assert row["yoy_pct"] is None
    assert row["reliability_level"] == "LOW_DATA"
    assert row["last_price_date"] == date(2026, 10, 10)
    assert row["as_of"] == date(2026, 10, 26)


def read_corridor(config):
    with get_connection(config["db"]) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute("""
                SELECT * FROM mart.market_corridor
                ORDER BY price_month, loading_city_gcid, unloading_city_gcid,
                         body_group, weight_category
            """).fetchall()


def test_market_corridor_filters_non_market_and_future_dates(core_config):
    as_of = date(2026, 10, 26)
    cases = [
        ({}, as_of),
        ({"is_fact": False}, as_of),
        ({"price_type": 6, "is_fact": True}, as_of),
        ({"complex": True}, as_of),
        ({"outlier_mark": True}, as_of),
        ({}, as_of + timedelta(days=1)),
        ({}, date(2026, 11, 1)),
        ({}, None),
    ]
    with get_connection(core_config["db"]) as connection:
        for price_id, (changes, price_date) in enumerate(cases, start=1):
            insert_price(connection, price_id, date(2026, 10, 1), **changes)
            if price_date is not None:
                connection.execute(
                    "INSERT INTO raw.price_dates (price_id, price_date) VALUES (%s, %s)",
                    (price_id, price_date),
                )
    load.load_core(core_config, date(2026, 10, 1), date(2026, 10, 1))

    load.refresh_market_corridor(core_config, as_of)

    rows = read_corridor(core_config)
    assert len(rows) == 1
    assert rows[0]["trips_count"] == 1
    assert rows[0]["carriers_count"] == 0
    assert rows[0]["cv_rate"] is None
    assert rows[0]["median_trip"] == 10000
    assert rows[0]["last_price_date"] == as_of


def test_market_corridor_compares_exact_calendar_months(core_config):
    history = [
        (date(2025, 10, 10), 6000),
        (date(2026, 7, 10), 4000),
        (date(2026, 9, 10), 10000),
        (date(2026, 10, 10), 12000),
    ]
    with get_connection(core_config["db"]) as connection:
        for price_id, (day, price) in enumerate(history, start=1):
            insert_price(connection, price_id, day, price=price)
            connection.execute(
                "INSERT INTO raw.price_dates (price_id, price_date) VALUES (%s, %s)",
                (price_id, day),
            )
        insert_price(connection, 10, date(2026, 9, 10), price=50000, total_weight=5)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (10, '2026-09-10');
        """)
    load.load_core(core_config, date(2025, 10, 10), date(2026, 10, 10))

    load.refresh_market_corridor(core_config, date(2026, 10, 26))

    rows = {
        row["price_month"]: row for row in read_corridor(core_config)
        if row["weight_category"] == "FTL"
    }
    october = rows[date(2026, 10, 1)]
    assert october["mom_pct"] == pytest.approx((12000 - 10000) / 10000 * 100)
    assert october["yoy_pct"] == pytest.approx((12000 - 6000) / 6000 * 100)
    assert rows[date(2026, 9, 1)]["mom_pct"] is None
    assert rows[date(2026, 7, 1)]["mom_pct"] is None
    assert rows[date(2026, 7, 1)]["yoy_pct"] is None


@pytest.mark.parametrize("trips,carriers,age,level", [
    (9, 5, 0, "LOW_DATA"),
    (10, 5, 0, "MEDIUM"),
    (29, 5, 0, "MEDIUM"),
    (30, 4, 0, "MEDIUM"),
    (30, 5, 0, "HIGH"),
    (30, 5, 30, "HIGH"),
    (30, 5, 31, "MEDIUM"),
])
def test_market_corridor_reliability_boundaries(core_config, trips, carriers, age, level):
    as_of = date(2026, 10, 26)
    day = as_of - timedelta(days=age)
    with get_connection(core_config["db"]) as connection:
        for index in range(trips):
            price_id = index + 1
            insert_price(
                connection, price_id, day,
                price_type=3, organization_id=index % carriers + 1,
            )
            connection.execute(
                "INSERT INTO raw.price_dates (price_id, price_date) VALUES (%s, %s)",
                (price_id, day),
            )
    load.load_core(core_config, day, day)

    load.refresh_market_corridor(core_config, as_of)

    row = read_corridor(core_config)[0]
    assert row["trips_count"] == trips
    assert row["carriers_count"] == carriers
    assert row["reliability_level"] == level
    assert row["cv_rate"] == 0


def test_market_corridor_repeats_and_reads_only_loaded_core(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day, price=2000)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01');
        """)
    load.load_core(core_config, day, day)
    load.refresh_market_corridor(core_config, day)
    before = read_corridor(core_config)
    with get_connection(core_config["db"]) as connection:
        connection.execute("UPDATE raw.prices SET price = 10000 WHERE id = 1")

    assert load.refresh_market_corridor(core_config, day) == 1
    assert read_corridor(core_config) == before

    load.load_core(core_config, day, day)
    load.refresh_market_corridor(core_config, day)
    assert read_corridor(core_config)[0]["median_trip"] == 10000


def test_market_corridor_rebuild_removes_rows_after_cutoff(core_config):
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, date(2026, 9, 1))
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-09-01'), (1, '2026-10-01');
        """)
    load.load_core(core_config, date(2026, 9, 1), date(2026, 9, 1))
    assert load.refresh_market_corridor(core_config, date(2026, 10, 26)) == 2

    assert load.refresh_market_corridor(core_config, date(2026, 9, 30)) == 1
    assert read_corridor(core_config)[0]["price_month"] == date(2026, 9, 1)
    assert load.refresh_market_corridor(core_config, date(2026, 8, 31)) == 0
    assert read_corridor(core_config) == []


def test_market_corridor_rolls_back_failed_rebuild(core_config):
    day = date(2026, 10, 1)
    with get_connection(core_config["db"]) as connection:
        insert_price(connection, 1, day, price=2000)
        connection.execute("""
            INSERT INTO raw.price_dates (price_id, price_date)
            VALUES (1, '2026-10-01');
        """)
    load.load_core(core_config, day, day)
    load.refresh_market_corridor(core_config, day)
    before = read_corridor(core_config)
    with get_connection(core_config["db"]) as connection:
        connection.execute("""
            ALTER TABLE mart.market_corridor
                ADD CONSTRAINT reject_high_price CHECK (median_trip < 5000);
            UPDATE core.fact_prices SET price_rub = 10000 WHERE price_id = 1;
        """)

    with pytest.raises(CheckViolation):
        load.refresh_market_corridor(core_config, date(2026, 10, 26))

    assert read_corridor(core_config) == before

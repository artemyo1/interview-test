import hashlib
import json
from datetime import date, timedelta
from importlib import import_module, util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from catboost import CatBoostRegressor
from psycopg import Connection, sql
from psycopg.errors import CheckViolation

from etl.load import load_core
from etl.db import get_connection
from test_core import core_config, insert_price

def feature_module():
    assert util.find_spec("ml.features") is not None, "Нет подготовки признаков ML"
    return import_module("ml.features")


def pipeline_module():
    assert util.find_spec("ml.pipeline") is not None, "Нет обучения и скоринга ML"
    return import_module("ml.pipeline")


def sample_frame():
    return pd.DataFrame([{
        "price_id": 1,
        "loading_date": date(2026, 10, 5),
        "loading_city_gcid": 1001,
        "unloading_city_gcid": 1003,
        "loading_region": None,
        "unloading_region": "Татарстан",
        "loading_latitude": np.nan,
        "loading_longitude": 37.6,
        "unloading_latitude": 55.8,
        "unloading_longitude": 49.1,
        "distance": 800,
        "total_weight": 20,
        "total_volume": 90,
        "body_group": "Борт|Тент",
        "weight_category": "FTL",
        "volume_category": "FTL",
        "actual_price_rub": 90000,
        "source_price": 1000,
        "exchange_rate": 90,
        "outlier_mark": False,
        "price_group_id": 42,
    }])


def test_features_use_only_allowed_inputs_and_encode_dates():
    module = feature_module()
    frame = sample_frame()

    features = module.build_features(frame)

    assert list(features.columns) == module.FEATURE_COLUMNS
    assert not {
        "price_id", "loading_date", "actual_price_rub", "source_price",
        "exchange_rate", "outlier_mark", "price_group_id",
    }.intersection(features.columns)
    assert features.loc[0, "loading_city_gcid"] == "1001"
    assert features.loc[0, "loading_region"] == "Не указан"
    assert features.loc[0, "body_group"] == "Борт|Тент"
    assert pd.isna(features.loc[0, "loading_latitude"])
    assert features.loc[0, "year"] == 2026
    assert features.loc[0, "month"] == 10
    assert features.loc[0, "day_of_week"] == date(2026, 10, 5).weekday()
    assert features.loc[0, "month_sin"] == pytest.approx(np.sin(2 * np.pi * 10 / 12))
    assert features.loc[0, "month_cos"] == pytest.approx(np.cos(2 * np.pi * 10 / 12))
    assert features.loc[0, "day_of_year_sin"] == pytest.approx(
        np.sin(2 * np.pi * date(2026, 10, 5).timetuple().tm_yday / 365.25)
    )
    changed = frame.copy()
    changed["actual_price_rub"] = 1
    changed["exchange_rate"] = 1
    changed["price_group_id"] = 999
    pd.testing.assert_frame_equal(module.build_features(changed), features)
    assert frame.loc[0, "loading_region"] is None


def test_split_data_uses_exact_time_boundaries():
    module = feature_module()
    split = getattr(module, "split_data", None)
    assert callable(split), "Нет временного разделения данных"
    frame = pd.DataFrame({
        "price_id": [6, 4, 1, 5, 3, 2],
        "loading_date": [
            date(2026, 10, 27), date(2026, 10, 5), date(2026, 7, 25),
            date(2026, 10, 26), date(2026, 10, 4), date(2026, 7, 26),
        ],
    })

    train, test, future = module.split_data(frame, date(2026, 10, 26))

    assert train["price_id"].tolist() == [2, 3]
    assert test["price_id"].tolist() == [4, 5]
    assert future["price_id"].tolist() == [6]
    assert module.date_windows(date(2026, 5, 31)) == (
        date(2026, 2, 28), date(2026, 5, 10)
    )


def test_ml_dataset_keeps_one_row_and_hides_future_prices(core_config):
    module = feature_module()
    load_dataset = getattr(module, "load_dataset", None)
    assert callable(load_dataset), "Нет чтения данных для ML"
    cases = [
        (date(2026, 7, 25), {}),
        (date(2026, 7, 26), {}),
        (date(2026, 10, 5), {}),
        (date(2026, 10, 26), {"is_fact": False}),
        (date(2026, 10, 26), {"complex": True}),
        (date(2026, 10, 26), {"outlier_mark": True}),
        (date(2026, 10, 26), {"price_type": 6, "is_fact": True}),
        (date(2026, 10, 27), {"is_fact": False, "outlier_mark": True}),
    ]
    with get_connection(core_config["db"]) as connection:
        for price_id, (day, changes) in enumerate(cases, start=1):
            insert_price(connection, price_id, day, **changes)
        connection.execute("""
            INSERT INTO raw.price_body_types (price_id, body_type_id)
            VALUES (2, 56), (2, 57), (2, 15);
        """)
    load_core(core_config, date(2026, 7, 25), date(2026, 10, 27))

    with get_connection(core_config["db"]) as connection:
        frame = module.load_dataset(connection, date(2026, 10, 26))

    assert frame["price_id"].tolist() == [2, 3, 8]
    assert frame["price_id"].is_unique
    assert frame.loc[frame["price_id"] == 2, "body_group"].item() == "Борт|Тент"
    assert frame.loc[frame["price_id"] == 3, "actual_price_rub"].item() == 10000
    assert pd.isna(frame.loc[frame["price_id"] == 8, "actual_price_rub"].item())
    assert "exchange_rate" not in frame
    assert len(module.build_features(frame)) == len(frame)


def test_metrics_are_calculated_on_actual_and_predicted_prices():
    module = pipeline_module()
    actual = np.array([1000, 2000, 4000], dtype=float)
    predicted = np.array([800, 2200, 3500], dtype=float)

    metrics = module.calculate_metrics(actual, predicted)

    errors = actual - predicted
    assert metrics["mae_rub"] == pytest.approx(np.mean(np.abs(errors)))
    assert metrics["rmse_rub"] == pytest.approx(np.sqrt(np.mean(errors ** 2)))
    assert metrics["mape_pct"] == pytest.approx(np.mean(np.abs(errors / actual)) * 100)
    assert metrics["r2"] == pytest.approx(
        1 - np.sum(errors ** 2) / np.sum((actual - np.mean(actual)) ** 2)
    )


def test_predictions_hide_future_actuals_and_errors():
    module = pipeline_module()
    frame = pd.concat([sample_frame(), sample_frame()], ignore_index=True)
    frame["price_id"] = [1, 2]
    frame["loading_date"] = [date(2026, 10, 5), date(2026, 10, 27)]
    frame["actual_price_rub"] = [1000, 999999]

    predictions = module.make_predictions(frame, np.array([800, 500]), date(2026, 10, 26))

    assert predictions["prediction_set"].tolist() == ["test", "future"]
    assert predictions.loc[0, "actual_price_rub"] == 1000
    assert predictions.loc[0, "abs_error_rub"] == abs(1000 - 800)
    assert predictions.loc[0, "ape_pct"] == pytest.approx(abs(1000 - 800) / 1000 * 100)
    assert pd.isna(predictions.loc[1, "actual_price_rub"])
    assert pd.isna(predictions.loc[1, "abs_error_rub"])
    assert pd.isna(predictions.loc[1, "ape_pct"])


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf])
def test_predictions_reject_invalid_prices(value):
    module = pipeline_module()

    with pytest.raises(ValueError, match="прогноз"):
        module.make_predictions(sample_frame(), np.array([value]), date(2026, 10, 26))


@pytest.fixture
def ml_config(core_config, tmp_path):
    core_config["model_dir"] = tmp_path
    with get_connection(core_config["db"]) as connection:
        for index in range(8):
            insert_price(
                connection, index + 1, date(2026, 7, 26) + timedelta(days=index * 7),
                price=5000 + index * 1000, distance=100 + index * 30,
            )
        insert_price(connection, 9, date(2026, 10, 5), price=15000, distance=500)
        insert_price(connection, 10, date(2026, 10, 26), price=18000, distance=600)
        insert_price(connection, 11, date(2026, 10, 27), price=90000, distance=700)
        insert_price(
            connection, 12, date(2026, 11, 5), price=99999, distance=800,
            is_fact=False, outlier_mark=True, body_type=None,
        )
    load_core(core_config, date(2026, 7, 26), date(2026, 11, 5))
    return core_config


def test_ml_trains_saves_model_and_writes_predictions(ml_config):
    module = pipeline_module()
    run_ml = getattr(module, "run_ml", None)
    assert callable(run_ml), "Нет запуска обучения и скоринга"

    report = module.run_ml(ml_config, date(2026, 10, 26))

    assert report["train_count"] == 8
    assert report["test_count"] == 2
    assert report["future_count"] == 2
    assert report["predictions_count"] == 4
    assert set(report["metrics"]) == {"r2", "mape_pct", "rmse_rub", "mae_rub"}
    assert all(np.isfinite(value) for value in report["metrics"].values())
    model_path = ml_config["model_dir"] / "catboost_price.cbm"
    report_path = ml_config["model_dir"] / "ml_report.json"
    assert model_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == report["model_sha256"]

    with get_connection(ml_config["db"]) as connection:
        rows = connection.execute("""
            SELECT price_id, actual_price_rub, predicted_price_rub, abs_error_rub, ape_pct
            FROM mart.ml_price_predictions ORDER BY price_id
        """).fetchall()
        frame = feature_module().load_dataset(connection, date(2026, 10, 26))
    assert [row[0] for row in rows] == [9, 10, 11, 12]
    assert all(row[1] is None and row[3] is None and row[4] is None for row in rows[2:])
    assert all(row[1] is not None and row[3] >= 0 and row[4] >= 0 for row in rows[:2])

    model = CatBoostRegressor()
    model.load_model(str(model_path))
    _, test, future = feature_module().split_data(frame, date(2026, 10, 26))
    score_frame = pd.concat([test, future], ignore_index=True)
    features = feature_module().build_features(score_frame)
    assert model.feature_names_ == feature_module().FEATURE_COLUMNS
    assert model.predict(features) == pytest.approx([row[2] for row in rows])


def read_predictions(config):
    with get_connection(config["db"]) as connection:
        return connection.execute("""
            SELECT price_id, loading_date, body_group, prediction_set,
                   actual_price_rub, predicted_price_rub, abs_error_rub, ape_pct,
                   model_name, as_of
            FROM mart.ml_price_predictions ORDER BY price_id
        """).fetchall()


def test_ml_repeat_does_not_learn_test_or_future_prices(ml_config):
    module = pipeline_module()
    as_of = date(2026, 10, 26)
    module.run_ml(ml_config, as_of)
    before = read_predictions(ml_config)
    with get_connection(ml_config["db"]) as connection:
        connection.execute("""
            UPDATE core.fact_prices SET price_rub = price_rub * 3
            WHERE loading_date >= DATE '2026-10-05'
        """)

    second = module.run_ml(ml_config, as_of)
    after = read_predictions(ml_config)

    assert [row[5] for row in before] == [row[5] for row in after]
    assert before[0][4] != after[0][4]
    assert all(row[4] is None and row[6] is None and row[7] is None for row in after[2:])
    third = module.run_ml(ml_config, as_of)
    assert read_predictions(ml_config) == after
    assert second["metrics"] == third["metrics"]


def test_ml_failed_copy_keeps_previous_predictions_and_model(ml_config):
    module = pipeline_module()
    module.run_ml(ml_config, date(2026, 10, 26))
    before = read_predictions(ml_config)
    model_path = ml_config["model_dir"] / "catboost_price.cbm"
    report_path = ml_config["model_dir"] / "ml_report.json"
    model_bytes = model_path.read_bytes()
    report_bytes = report_path.read_bytes()
    with get_connection(ml_config["db"]) as connection:
        row = connection.execute("SELECT max(scored_at) FROM mart.ml_price_predictions").fetchone()
        assert row is not None
        connection.execute(sql.SQL("""
            ALTER TABLE mart.ml_price_predictions ADD CONSTRAINT freeze_scores
            CHECK (scored_at <= {})
        """).format(sql.Literal(row[0])))

    with pytest.raises(CheckViolation):
        module.run_ml(ml_config, date(2026, 10, 26))

    assert read_predictions(ml_config) == before
    assert model_path.read_bytes() == model_bytes
    assert report_path.read_bytes() == report_bytes


def read_full_predictions(config):
    with get_connection(config["db"]) as connection:
        row = connection.execute("SELECT to_regclass('mart.ml_price_predictions')").fetchone()
        assert row is not None
        if row[0] is None:
            return None
        return connection.execute(
            "SELECT * FROM mart.ml_price_predictions ORDER BY price_id"
        ).fetchall()


def assert_ml_lock_held(config):
    with get_connection(config["db"]) as connection:
        row = connection.execute("SELECT pg_try_advisory_lock(20261026)").fetchone()
        assert row is not None
        available = row[0]
        if available:
            connection.execute("SELECT pg_advisory_unlock(20261026)")
    assert not available, "Блокировка ML освобождена до завершения публикации"


def test_ml_publication_holds_canonical_lock(ml_config, monkeypatch):
    module = pipeline_module()
    original_replace = Path.replace
    publications = []

    def checked_replace(source, target):
        target = Path(target)
        if target.parent == ml_config["model_dir"]:
            assert_ml_lock_held(ml_config)
            publications.append(target.name)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", checked_replace)

    module.run_ml(ml_config, date(2026, 10, 26))

    assert publications == ["catboost_price.cbm", "ml_report.json"]
    assert_ml_lock_released(ml_config)


def assert_ml_lock_released(config):
    with get_connection(config["db"]) as connection:
        row = connection.execute("SELECT pg_try_advisory_lock(20261026)").fetchone()
        assert row is not None
        available = row[0]
        if available:
            connection.execute("SELECT pg_advisory_unlock(20261026)")
    assert available, "Блокировка ML осталась после завершения команды"


@pytest.mark.parametrize("previous_run", [True, False])
@pytest.mark.parametrize("failure", ["second_replace", "commit"])
def test_ml_publication_failure_restores_previous_state(
    ml_config, monkeypatch, previous_run, failure,
):
    module = pipeline_module()
    as_of = date(2026, 10, 26)
    paths = [ml_config["model_dir"] / name for name in ("catboost_price.cbm", "ml_report.json")]
    if previous_run:
        module.run_ml(ml_config, as_of)
    before_rows = read_full_predictions(ml_config)
    before_files = [path.read_bytes() if path.exists() else None for path in paths]
    ml_config["ml_seed"] += 1
    original_replace = Path.replace
    original_commit = Connection.commit
    publications = []
    restorations = []
    commit_publications = []
    failed = False
    target_connection = get_connection(ml_config["db"])
    monkeypatch.setattr(module, "get_connection", lambda config: target_connection)

    def checked_replace(source, target):
        nonlocal failed
        target = Path(target)
        if target in paths:
            if failed:
                assert_ml_lock_held(ml_config)
                restorations.append(target.name)
            else:
                publications.append(target.name)
                if failure == "second_replace" and target == paths[1]:
                    failed = True
                    raise OSError("injected publication failure")
        return original_replace(source, target)

    def failed_commit(connection):
        nonlocal failed
        if connection is target_connection and failure == "commit":
            commit_publications.extend(publications)
            failed = True
            raise OSError("injected publication failure")
        return original_commit(connection)

    monkeypatch.setattr(Path, "replace", checked_replace)
    monkeypatch.setattr(Connection, "commit", failed_commit)

    with pytest.raises(OSError, match="injected publication failure"):
        module.run_ml(ml_config, as_of)

    assert read_full_predictions(ml_config) == before_rows
    assert [path.read_bytes() if path.exists() else None for path in paths] == before_files
    assert target_connection.closed
    assert_ml_lock_released(ml_config)
    if failure == "commit":
        assert commit_publications == [path.name for path in paths]
    if previous_run:
        assert restorations


def test_ml_backup_read_failure_does_not_publish(ml_config, monkeypatch):
    module = pipeline_module()
    as_of = date(2026, 10, 26)
    module.run_ml(ml_config, as_of)
    paths = [ml_config["model_dir"] / name for name in ("catboost_price.cbm", "ml_report.json")]
    before_rows = read_full_predictions(ml_config)
    before_files = [path.read_bytes() for path in paths]
    original_read = Path.read_bytes
    original_replace = Path.replace
    publications = []
    failed = False

    def failed_read(path):
        nonlocal failed
        if path == paths[1] and not failed:
            assert_ml_lock_held(ml_config)
            failed = True
            raise OSError("injected backup read failure")
        return original_read(path)

    def checked_replace(source, target):
        if Path(target) in paths:
            publications.append(Path(target).name)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "read_bytes", failed_read)
    monkeypatch.setattr(Path, "replace", checked_replace)

    with pytest.raises(OSError, match="injected backup read failure"):
        module.run_ml(ml_config, as_of)

    assert not publications
    assert read_full_predictions(ml_config) == before_rows
    assert [path.read_bytes() for path in paths] == before_files
    assert_ml_lock_released(ml_config)


@pytest.mark.parametrize("start,end", [
    (date(2026, 7, 26), date(2026, 10, 4)),
    (date(2026, 10, 5), date(2026, 10, 26)),
])
def test_ml_rejects_insufficient_train_or_test(ml_config, start, end):
    with get_connection(ml_config["db"]) as connection:
        connection.execute(
            "DELETE FROM core.fact_prices WHERE loading_date BETWEEN %s AND %s", (start, end)
        )

    with pytest.raises(ValueError, match="две сделки"):
        pipeline_module().run_ml(ml_config, date(2026, 10, 26))

    assert not (ml_config["model_dir"] / "catboost_price.cbm").exists()


def test_ml_scores_test_when_no_future_requests_exist(ml_config):
    with get_connection(ml_config["db"]) as connection:
        connection.execute("DELETE FROM core.fact_prices WHERE loading_date > DATE '2026-10-26'")

    report = pipeline_module().run_ml(ml_config, date(2026, 10, 26))

    assert report["future_count"] == 0
    assert report["predictions_count"] == report["test_count"] == 2
    assert len(read_predictions(ml_config)) == 2

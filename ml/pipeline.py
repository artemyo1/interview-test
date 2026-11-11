import hashlib
import json
import logging
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import LiteralString, cast

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    r2_score,
    root_mean_squared_error,
)

from etl.db import get_connection
from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    build_features,
    date_windows,
    load_dataset,
    split_data,
)

MODEL_NAME = "catboost_v1"


def calculate_metrics(actual, predicted):
    return {
        "r2": float(r2_score(actual, predicted)),
        "mape_pct": float(mean_absolute_percentage_error(actual, predicted) * 100),
        "rmse_rub": float(root_mean_squared_error(actual, predicted)),
        "mae_rub": float(mean_absolute_error(actual, predicted)),
    }


def make_predictions(frame, predicted, as_of):
    predicted = np.asarray(predicted, dtype=float)
    if len(predicted) != len(frame) or not np.isfinite(predicted).all() or (predicted <= 0).any():
        raise ValueError("Модель вернула недопустимый прогноз цены")

    result = frame[[
        "price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_group"
    ]].copy()
    result["loading_date"] = pd.to_datetime(result["loading_date"]).dt.date
    future = result["loading_date"] > as_of
    result["prediction_set"] = np.where(future, "future", "test")
    result["actual_price_rub"] = frame["actual_price_rub"].where(~future, np.nan)
    result["predicted_price_rub"] = predicted
    result["abs_error_rub"] = abs(result["actual_price_rub"] - predicted)
    result["ape_pct"] = result["abs_error_rub"] / result["actual_price_rub"] * 100
    result["model_name"] = MODEL_NAME
    result["as_of"] = as_of
    result["scored_at"] = datetime.now(UTC)
    return result


def run_ml(config, as_of):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()
    context = {"step": "ml", "as_of": as_of.isoformat()}
    logger.info(
        "Начало обучения и скоринга",
        extra={"context": {**context, "status": "started"}},
    )
    parameters = {
        "iterations": 400,
        "depth": 6,
        "learning_rate": 0.05,
        "loss_function": "RMSE",
        "random_seed": config["ml_seed"],
        "thread_count": 1,
        "task_type": "CPU",
        "allow_writing_files": False,
        "verbose": False,
    }
    model_dir = Path(config["model_dir"])
    model_dir.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "003_ml.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    with TemporaryDirectory(dir=model_dir) as directory:
        model_path = Path(directory) / "catboost_price.cbm"
        report_path = Path(directory) / "ml_report.json"
        with closing(get_connection(config["db"])) as connection:
            connection.execute("SELECT pg_advisory_lock(20261026)")
            frame = load_dataset(connection, as_of)
            train, test, future = split_data(frame, as_of)
            if len(train) < 2 or len(test) < 2:
                raise ValueError("Для обучения и проверки нужно хотя бы по две сделки")
            logger.info(
                "Данные для ML подготовлены",
                extra={
                    "context": {
                        **context,
                        "train_count": len(train),
                        "test_count": len(test),
                        "future_count": len(future),
                    }
                },
            )

            model = CatBoostRegressor(**parameters)
            model.fit(
                build_features(train), train["actual_price_rub"],
                cat_features=CATEGORICAL_FEATURES,
            )
            score_frame = pd.concat([test, future], ignore_index=True)
            predicted = model.predict(build_features(score_frame))
            predictions = make_predictions(score_frame, predicted, as_of)
            metrics = calculate_metrics(test["actual_price_rub"], predicted[:len(test)])
            if not all(np.isfinite(value) for value in metrics.values()):
                raise ValueError("Не удалось рассчитать конечные метрики качества")

            connection.execute(cast(LiteralString, schema_sql))
            connection.execute("DELETE FROM mart.ml_price_predictions")
            values = predictions.astype(object).where(pd.notna(predictions), None)
            with connection.cursor().copy("""
                COPY mart.ml_price_predictions (
                    price_id, loading_date, loading_city_gcid, unloading_city_gcid,
                    body_group, prediction_set, actual_price_rub, predicted_price_rub,
                    abs_error_rub, ape_pct, model_name, as_of, scored_at
                ) FROM STDIN
            """) as copy:
                for row in values.itertuples(index=False, name=None):
                    copy.write_row(row)

            model.save_model(str(model_path))
            train_start, test_start = date_windows(as_of)
            report = {
                "model_name": MODEL_NAME,
                "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                "as_of": as_of.isoformat(),
                "train_start": train_start.isoformat(),
                "train_end_exclusive": test_start.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": as_of.isoformat(),
                "train_count": len(train),
                "test_count": len(test),
                "future_count": len(future),
                "predictions_count": len(predictions),
                "metrics": metrics,
                "parameters": parameters,
                "features": FEATURE_COLUMNS,
                "categorical_features": CATEGORICAL_FEATURES,
            }
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            artifacts = []
            for staged in (model_path, report_path):
                target = model_dir / staged.name
                backup = None
                if target.exists():
                    backup = Path(directory) / (staged.name + ".backup")
                    backup.write_bytes(target.read_bytes())
                artifacts.append((staged, target, backup))

            published = []
            try:
                for staged, target, backup in artifacts:
                    staged.replace(target)
                    published.append((target, backup))
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                finally:
                    for target, backup in reversed(published):
                        if backup is None:
                            target.unlink(missing_ok=True)
                        else:
                            backup.replace(target)
                raise

    logger.info(
        "Обучение и скоринг завершены",
        extra={
            "context": {
                **context,
                "status": "success",
                "train_count": report["train_count"],
                "test_count": report["test_count"],
                "future_count": report["future_count"],
                "predictions_count": report["predictions_count"],
                **metrics,
                "duration_seconds": round(perf_counter() - started_at, 3),
            }
        },
    )
    return report

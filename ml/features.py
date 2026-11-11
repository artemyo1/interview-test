from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import LiteralString, cast

import numpy as np
import pandas as pd


CATEGORICAL_FEATURES = [
    "loading_city_gcid",
    "unloading_city_gcid",
    "loading_region",
    "unloading_region",
    "body_group",
    "weight_category",
    "volume_category",
]

NUMERIC_FEATURES = [
    "distance",
    "total_weight",
    "total_volume",
    "loading_latitude",
    "loading_longitude",
    "unloading_latitude",
    "unloading_longitude",
]

DATE_FEATURES = [
    "year",
    "month",
    "day_of_week",
    "day_of_year",
    "month_sin",
    "month_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "day_of_year_sin",
    "day_of_year_cos",
]

FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES + DATE_FEATURES


def build_features(frame):
    features = frame[CATEGORICAL_FEATURES + NUMERIC_FEATURES].copy()
    for column in CATEGORICAL_FEATURES:
        features[column] = features[column].fillna("Не указан").astype(str)
    for column in NUMERIC_FEATURES:
        features[column] = pd.to_numeric(features[column])

    dates = pd.to_datetime(frame["loading_date"])
    features["year"] = dates.dt.year
    features["month"] = dates.dt.month
    features["day_of_week"] = dates.dt.day_of_week
    features["day_of_year"] = dates.dt.day_of_year
    for column, period in [("month", 12), ("day_of_week", 7), ("day_of_year", 365.25)]:
        angle = 2 * np.pi * features[column] / period
        features[column + "_sin"] = np.sin(angle)
        features[column + "_cos"] = np.cos(angle)

    return features[FEATURE_COLUMNS]


def date_windows(as_of):
    year = as_of.year
    month = as_of.month - 3
    if month <= 0:
        month += 12
        year -= 1
    day = min(as_of.day, monthrange(year, month)[1])
    train_start = date(year, month, day)
    test_start = as_of - timedelta(weeks=3)
    return train_start, test_start


def split_data(frame, as_of):
    frame = frame.sort_values(["loading_date", "price_id"]).copy()
    dates = pd.to_datetime(frame["loading_date"]).dt.date
    train_start, test_start = date_windows(as_of)
    train = frame.loc[(dates >= train_start) & (dates < test_start)].copy()
    test = frame.loc[(dates >= test_start) & (dates <= as_of)].copy()
    future = frame.loc[dates > as_of].copy()
    return train, test, future


def load_dataset(connection, as_of):
    train_start, _ = date_windows(as_of)
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "040_ml_dataset.sql"
    query = sql_path.read_text(encoding="utf-8")
    cursor = connection.execute(
        cast(LiteralString, query), {"train_start": train_start, "as_of": as_of}
    )
    if cursor.description is None:
        raise RuntimeError("Запрос ML не вернул набор данных")
    columns = [column.name for column in cursor.description]
    return pd.DataFrame.from_records(cursor.fetchall(), columns=columns)

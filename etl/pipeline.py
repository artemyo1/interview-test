import logging
from time import perf_counter

from etl.bootstrap import initialize_source
from etl.db import get_connection
from etl.load import load_core, refresh_market_corridor
from ml.pipeline import run_ml


def run_pipeline(config, as_of):
    logger = logging.getLogger("logistics")
    started_at = perf_counter()
    context = {"step": "pipeline", "as_of": as_of.isoformat()}
    logger.info(
        "Начало общего конвейера",
        extra={"context": {**context, "status": "started"}},
    )

    initialize_source(config)
    with get_connection(config["db"]) as connection:
        dates = connection.execute(
            "SELECT min(loading_date), max(loading_date) FROM raw.prices"
        ).fetchone()
    if dates is None or dates[0] is None or dates[1] is None:
        raise ValueError("Источник не содержит сделок для обработки")

    load_core(config, dates[0], dates[1])
    refresh_market_corridor(config, as_of)
    report = run_ml(config, as_of)

    logger.info(
        "Общий конвейер завершён",
        extra={
            "context": {
                **context,
                "status": "success",
                "duration_seconds": round(perf_counter() - started_at, 3),
            }
        },
    )
    return report

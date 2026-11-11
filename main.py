import argparse
import subprocess
from datetime import date

import psycopg
from catboost import CatBoostError

from etl.bootstrap import initialize_source
from etl.config import load_config
from etl.load import load_core, refresh_dimensions, refresh_market_corridor
from etl.logging_config import configure_logging
from etl.pipeline import run_pipeline
from ml.pipeline import run_ml


def main():
    parser = argparse.ArgumentParser(
        description="Конвейер аналитики тарифов грузоперевозок",
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "check-config",
        help="Проверить настройки без подключения к БД",
    )

    commands.add_parser("init", help="Создать и заполнить исходную схему raw")

    commands.add_parser(
        "dimensions", help="Создать объекты Core и обновить справочники"
    )

    core_command = commands.add_parser(
        "load-core", help="Загрузить подготовленные сделки за период"
    )
    core_command.add_argument("--start-date", type=date.fromisoformat, required=True)
    core_command.add_argument("--end-date", type=date.fromisoformat, required=True)

    mart_command = commands.add_parser(
        "refresh-mart", help="Пересчитать рыночную витрину из данных Core"
    )
    mart_command.add_argument("--as-of", type=date.fromisoformat)

    ml_command = commands.add_parser(
        "train-ml", help="Обучить модель и сохранить тестовые и будущие прогнозы"
    )
    ml_command.add_argument("--as-of", type=date.fromisoformat)

    run_command = commands.add_parser("run", help="Выполнить весь конвейер")
    run_command.add_argument("--as-of", type=date.fromisoformat)

    args = parser.parse_args()
    if args.command == "load-core" and args.start_date > args.end_date:
        parser.error("start_date должна быть не позже end_date")

    logger = configure_logging()

    try:
        config = load_config()
    except ValueError as error:
        parser.error(str(error))

    if args.command == "check-config":
        logger.info(
            "Конфигурация корректна",
            extra={"context": {"step": "check_config", "status": "success"}},
        )
    elif args.command == "init":
        try:
            initialize_source(config)
            refresh_dimensions(config)
        except (subprocess.CalledProcessError, OSError):
            logger.error(
                "Не удалось инициализировать источник; "
                "проверь вывод psql и наличие клиента",
                extra={
                    "context": {
                        "step": "init",
                        "status": "failed",
                    }
                },
            )
            raise SystemExit(1) from None
    elif args.command == "dimensions":
        refresh_dimensions(config)
    elif args.command == "load-core":
        try:
            load_core(config, args.start_date, args.end_date)
        except (psycopg.Error, OSError, RuntimeError):
            logger.exception(
                "Не удалось загрузить Core; изменения отменены",
                extra={"context": {"step": "load_core", "status": "failed"}},
            )
            raise SystemExit(1) from None
    elif args.command == "refresh-mart":
        try:
            refresh_market_corridor(config, args.as_of or config["as_of"])
        except (psycopg.Error, OSError, RuntimeError):
            logger.exception(
                "Не удалось пересчитать витрину; изменения отменены",
                extra={"context": {"step": "market_corridor", "status": "failed"}},
            )
            raise SystemExit(1) from None
    elif args.command == "train-ml":
        try:
            run_ml(config, args.as_of or config["as_of"])
        except (psycopg.Error, OSError, ValueError, RuntimeError, CatBoostError):
            logger.exception(
                "Не удалось обучить модель или сохранить прогнозы",
                extra={"context": {"step": "ml", "status": "failed"}},
            )
            raise SystemExit(1) from None
    elif args.command == "run":
        try:
            run_pipeline(config, args.as_of or config["as_of"])
        except (
            subprocess.CalledProcessError, psycopg.Error, OSError,
            ValueError, RuntimeError, CatBoostError,
        ):
            logger.exception(
                "Общий конвейер не завершён",
                extra={"context": {"step": "pipeline", "status": "failed"}},
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()

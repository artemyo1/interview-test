import argparse
import subprocess

from etl.bootstrap import initialize_source
from etl.config import load_config
from etl.load import refresh_dimensions
from etl.logging_config import configure_logging


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

    args = parser.parse_args()

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
        except subprocess.CalledProcessError, OSError:
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


if __name__ == "__main__":
    main()

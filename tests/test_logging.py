import json
import logging

from etl.logging_config import JsonFormatter


def test_json_formatter():
    record = logging.LogRecord(
        name="logistics",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Конфигурация корректна",
        args=(),
        exc_info=None,
    )
    record.context = {
        "step": "check-config",
        "status": "success",
    }

    formatted = JsonFormatter().format(record)

    assert formatted.startswith("{")

    data = json.loads(formatted)

    assert data["message"] == "Конфигурация корректна"
    assert data["level"] == "INFO"
    assert data["context"]["step"] == "check-config"
    assert data["context"]["status"] == "success"
    assert "timestamp" in data

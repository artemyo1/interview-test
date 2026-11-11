from datetime import date
from importlib import import_module, util
from unittest.mock import MagicMock

import pytest


def pipeline_module():
    assert util.find_spec("etl.pipeline") is not None, "Нет общего конвейера"
    return import_module("etl.pipeline")


def configure_steps(monkeypatch, dates):
    module = pipeline_module()
    calls = []
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = dates
    monkeypatch.setattr(module, "get_connection", lambda config: connection)
    monkeypatch.setattr(module, "initialize_source", lambda config: calls.append("init"))
    monkeypatch.setattr(
        module, "load_core", lambda config, start, end: calls.append(("core", start, end))
    )
    monkeypatch.setattr(
        module, "refresh_market_corridor", lambda config, as_of: calls.append(("mart", as_of))
    )

    def run_ml(config, as_of):
        calls.append(("ml", as_of))
        return {"metrics": {"mae_rub": 100}}

    monkeypatch.setattr(module, "run_ml", run_ml)
    return module, calls


def test_pipeline_runs_all_steps_in_order(monkeypatch):
    start = date(2023, 10, 3)
    end = date(2027, 2, 13)
    as_of = date(2026, 10, 26)
    module, calls = configure_steps(monkeypatch, (start, end))

    result = module.run_pipeline({"db": {}}, as_of)

    assert calls == ["init", ("core", start, end), ("mart", as_of), ("ml", as_of)]
    assert result == {"metrics": {"mae_rub": 100}}


def test_pipeline_stops_after_failed_step(monkeypatch):
    module, calls = configure_steps(monkeypatch, (date(2026, 7, 26), date(2026, 10, 26)))

    def failed_load(*args):
        raise RuntimeError("Ошибка загрузки Core")

    monkeypatch.setattr(module, "load_core", failed_load)
    with pytest.raises(RuntimeError, match="Core"):
        module.run_pipeline({"db": {}}, date(2026, 10, 26))

    assert calls == ["init"]


@pytest.mark.parametrize("dates", [None, (None, None)])
def test_pipeline_rejects_empty_source(monkeypatch, dates):
    module, calls = configure_steps(monkeypatch, dates)

    with pytest.raises(ValueError, match="Источник"):
        module.run_pipeline({"db": {}}, date(2026, 10, 26))

    assert calls == ["init"]

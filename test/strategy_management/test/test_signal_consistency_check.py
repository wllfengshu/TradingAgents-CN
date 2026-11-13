"""一致性校验 CLI：只测参数解析，不打真实 API。"""

import pytest

from zstock.strategy_management.script import signal_consistency_check as scc


def test_main_requires_date(monkeypatch):
    monkeypatch.setattr("sys.argv", ["signal_consistency_check"])
    with pytest.raises(SystemExit):
        scc.main()


def test_main_parses_dates_and_dispatches(monkeypatch):
    called = {}

    async def _fake(dates, include_pipeline, tolerance):
        called["dates"] = dates
        called["include_pipeline"] = include_pipeline
        called["tolerance"] = tolerance
        return 0

    monkeypatch.setattr(scc, "_run_checks", _fake)
    monkeypatch.setattr(
        "sys.argv",
        [
            "signal_consistency_check",
            "--date",
            "2024-06-03",
            "--dates",
            "2024-06-03,2024-01-02",
            "--no-pipeline",
            "--tolerance",
            "1e-5",
        ],
    )
    assert scc.main() == 0
    assert called["dates"][0] == "2024-06-03"
    assert "2024-01-02" in called["dates"]
    assert called["include_pipeline"] is False
    assert called["tolerance"] == 1e-5

"""Local UI serialization and request validation contracts; no external services."""

import pytest
import pandas as pd
from tradingagents.web.server import symbol, MARKDOWN
from tradingagents.dataflows.web_data import frame_data, number


def test_symbol_validation():
    assert symbol("700.hk") == "0700.HK"
    assert symbol("600519.SH") == "600519.SS"
    for value in ("", "../secret", "<script>", None):
        with pytest.raises(ValueError):
            symbol(value)


def test_financial_json_preserves_missing_values_and_periods():
    frame = pd.DataFrame(
        {pd.Timestamp("2025-12-31"): [12, float("nan")]}, index=["Revenue", "Profit"]
    )
    result = frame_data(frame)
    assert result["periods"] == ["2025-12-31"]
    assert result["rows"][1]["values"] == [None]
    assert number(float("inf")) is None
    assert number(0) == 0


def test_untrusted_report_html_is_escaped():
    output = MARKDOWN.render("<script>alert(1)</script>\n\n[bad](javascript:alert(1))")
    assert "<script>" not in output
    assert 'href="javascript:' not in output


def test_background_job_persists_reports_without_exposing_engine_config(monkeypatch, tmp_path):
    from tradingagents.web import server
    from tradingagents.graph import trading_graph

    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(
        server,
        "JOBS",
        {"test": {"id": "test", "ticker": "AAPL", "peers": [], "date": "2026-11-12"}},
    )
    monkeypatch.setattr(
        server, "company_data", lambda ticker: {"symbol": ticker, "profile": {}, "statements": {}}
    )

    class Graph:
        def __init__(self, selected_analysts, config, research_snapshot, on_report=None):
            assert selected_analysts == ["fundamentals", "operating", "metrics", "news"]
            assert config["checkpoint_enabled"] is False
            assert research_snapshot["company"]["symbol"] == "AAPL"

        def run_settings(self):
            return {}

        def propagate(self, ticker, date):
            return {"fundamentals_report": "Evidence", "final_trade_decision": "Hold"}, "Hold"

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(server, "get_filing_documents", lambda *a: {"documents": []})
    server.run_job("test")
    assert server.JOBS["test"]["status"] == "completed"
    assert server.JOBS["test"]["reports"]["fundamentals_report"] == "Evidence"
    assert (tmp_path / "test.json").exists()
    assert "config" not in server.JOBS["test"]


def test_worker_failure_retains_data_and_redacts_exception(monkeypatch, tmp_path):
    from tradingagents.web import server
    from tradingagents.graph import trading_graph

    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(
        server,
        "JOBS",
        {"test": {"id": "test", "ticker": "AAPL", "peers": [], "date": "2026-11-12"}},
    )
    monkeypatch.setattr(server, "company_data", lambda ticker: {"symbol": ticker})

    class Graph:
        def __init__(self, **kwargs):
            raise RuntimeError("secret-token")

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    server.run_job("test")
    result = server.JOBS["test"]
    assert result["status"] == "failed"
    assert result["data"]["symbol"] == "AAPL"
    assert "secret-token" not in result["error"]


def test_ui_reads_the_canonical_snapshot(monkeypatch, tmp_path):
    from types import SimpleNamespace

    snapshot = {"id": "canonical", "company": {"symbol": "AAPL"}, "peers": []}
    from tradingagents.web import server

    monkeypatch.setattr(
        server,
        "JOBS",
        {
            "one": {
                "id": "one",
                "research_snapshot": snapshot,
                "data": {"stale": True},
                "peers_data": [],
            }
        },
    )
    monkeypatch.setattr(server, "STORE", tmp_path)
    response = []
    handler = object.__new__(server.Handler)
    handler.path = "/api/jobs/one"
    handler.headers = {"Host": "127.0.0.1:8765"}
    handler.server = SimpleNamespace(server_port=8765)
    handler.send = lambda payload, status=200: response.append((status, payload))
    handler.do_GET()
    status, data = response[0]
    assert status == 200
    assert data["data"] == snapshot["company"]
    assert data["peers_data"] == snapshot["peers"]

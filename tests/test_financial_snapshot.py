"""Snapshot evidence is shared through the real graph, without vendor refetches."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.financial_snapshot import snapshot_context
from tradingagents.dataflows.date_window import get_current_date
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.fixture
def snapshot():
    return {
        "id": "snapshot-one",
        "schema_version": 1,
        "as_of_date": get_current_date(),
        "company": {
            "symbol": "AAPL",
            "source": "Yahoo Finance / yfinance",
            "retrieved_at": get_current_date() + "T10:00:00+08:00",
            "profile": {
                "longName": "Apple",
                "currency": "USD",
                "financialCurrency": "USD",
                "returnOnEquity": 0.25,
                "debtToEquity": 125.0,
            },
            "statements": {
                "annual": {
                    "income": {
                        "periods": ["2025-09-30"],
                        "rows": [{"name": "Total Revenue", "values": [1234.0]}],
                    }
                }
            },
            "errors": [],
        },
        "peers": [{"symbol": "MSFT", "trailingPE": 31.25, "returnOnEquity": None}],
    }


def make_graph(monkeypatch, tmp_path, snapshot, selected=None):
    from tradingagents.graph import trading_graph
    from tradingagents.default_config import build_default_config

    seen = []

    class LLM:
        def invoke(self, prompt):
            seen.append(str(prompt))
            if "You are the Operating Performance Analyst" in str(prompt):
                return AIMessage(content="Operating evidence-based conclusion")
            if "You are the Financial Metrics Analyst" in str(prompt):
                return AIMessage(content="Metrics evidence-based conclusion")
            return AIMessage(content="**Rating**: Hold\nEvidence checked.")

        def with_structured_output(self, *args, **kwargs):
            raise NotImplementedError

        def bind_tools(self, *args, **kwargs):
            pytest.fail("Snapshot mode must not bind financial tools")

    monkeypatch.setattr(
        trading_graph, "create_tier_client", lambda *a, **k: SimpleNamespace(get_llm=lambda: LLM())
    )
    monkeypatch.setattr(
        trading_graph,
        "resolve_instrument_identity",
        lambda *a: pytest.fail("Duplicate identity fetch"),
    )
    monkeypatch.setattr(
        TradingAgentsGraph,
        "_memory_step",
        lambda self, state: {"past_context": "", "memory_note": ""},
    )
    config = build_default_config()
    config.update(
        results_dir=str(tmp_path / "results"),
        data_cache_dir=str(tmp_path / "cache"),
        memory_log_path=str(tmp_path / "memory.md"),
        checkpoint_enabled=False,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
    )
    return TradingAgentsGraph(
        selected or ["fundamentals"], config=config, research_snapshot=snapshot
    ), seen


def test_real_graph_uses_frozen_evidence_for_every_role(monkeypatch, tmp_path, snapshot):
    import yfinance

    monkeypatch.setattr(
        yfinance, "Ticker", lambda *a: pytest.fail("No vendor fetch during snapshot analysis")
    )
    original = deepcopy(snapshot)
    graph, seen = make_graph(monkeypatch, tmp_path, snapshot)
    snapshot["peers"][0]["trailingPE"] = 999  # caller changes must not alter the run
    state, rating = graph.propagate("AAPL", original["as_of_date"])
    assert rating == "Hold"
    assert len(seen) == 9  # analyst + bull/bear + managers/trader + three risk roles
    assert all("snapshot-one" in text and "MSFT" in text and "31.25" in text for text in seen)
    assert "1234.0" in seen[0]  # the exact statement values shown by the UI
    assert state["research_snapshot"] == original
    path = next((tmp_path / "results").rglob("full_states_log_*.json"))
    assert json.loads(path.read_text())["research_snapshot"] == original


@pytest.mark.parametrize("ticker,date", [("MSFT", None), ("AAPL", "2020-01-01")])
def test_snapshot_cannot_be_used_for_another_instrument_or_historical_run(
    monkeypatch, tmp_path, snapshot, ticker, date
):
    graph, _ = make_graph(monkeypatch, tmp_path, snapshot)
    with pytest.raises(ValueError, match="snapshot"):
        graph.create_run_state(ticker, date or snapshot["as_of_date"])


def test_missing_peer_values_and_units_remain_explicit(snapshot):
    text = snapshot_context(snapshot, full=True)
    assert '"returnOnEquity": null' in text
    assert "already a percentage" in text
    assert "cumulative" in text
    assert "1234.0" in text


def test_company_and_peer_fetch_each_provider_field_once(monkeypatch):
    import pandas as pd
    import yfinance
    from tradingagents.dataflows.web_data import company_data, peer_data

    calls = []

    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
            calls.append((ticker, "create"))

        @property
        def info(self):
            calls.append((self.ticker, "info"))
            return {"longName": self.ticker, "trailingPE": float("nan")}

        def __getattr__(self, name):
            calls.append((self.ticker, name))
            return pd.DataFrame({pd.Timestamp("2025-12-31"): [1234.0]}, index=["Revenue"])

    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    company = company_data("AAPL")
    peer = peer_data("MSFT")
    assert len(calls) == 10  # one constructor + info + six tables, peer constructor + info
    assert calls.count(("AAPL", "info")) == 1
    assert company["profile"]["trailingPE"] is None
    assert peer["trailingPE"] is None
    assert peer["source"] == company["source"]


def test_specialists_feed_web_sections_and_downstream_research(monkeypatch, tmp_path, snapshot):
    import yfinance
    from tradingagents.reporting import write_report_tree

    monkeypatch.setattr(yfinance, "Ticker", lambda *a: pytest.fail("Duplicate vendor fetch"))
    graph, seen = make_graph(
        monkeypatch, tmp_path, snapshot, selected=["fundamentals", "operating", "metrics"]
    )
    state, rating = graph.propagate("AAPL", snapshot["as_of_date"])
    assert rating == "Hold"
    assert len(seen) == 11
    assert "Operating evidence-based conclusion" in state["operating_report"]
    assert "Metrics evidence-based conclusion" in state["metrics_report"]
    assert "snapshot-one" in state["operating_report"]
    # The fan-in waits for all three reports before the bull/bear debate.
    assert all(
        "Operating evidence-based conclusion" in prompt
        and "Metrics evidence-based conclusion" in prompt
        for prompt in seen[3:]
    )
    write_report_tree(state, "AAPL", tmp_path / "export")
    assert (tmp_path / "export/1_analysts/operating.md").exists()
    assert (tmp_path / "export/1_analysts/metrics.md").exists()
    path = next((tmp_path / "results").rglob("full_states_log_*.json"))
    assert json.loads(path.read_text())["metrics_report"] == state["metrics_report"]

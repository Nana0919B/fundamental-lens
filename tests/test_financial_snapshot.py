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


def make_graph(
    monkeypatch,
    tmp_path,
    snapshot,
    selected=None,
    allow_news=False,
    macro_context=None,
    seed_reports=None,
    market_environment=None,
):
    from tradingagents.default_config import build_default_config
    from tradingagents.graph import trading_graph

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

        def bind_tools(self, tools, **kwargs):
            if not allow_news:
                pytest.fail("Snapshot mode must not bind financial tools")
            from langchain_core.runnables import RunnableLambda

            expected = {"get_news", "get_global_news", "get_prediction_markets"}
            if macro_context is None:
                expected.add("get_macro_indicators")
            assert {t.name for t in tools} == expected

            def news(prompt):
                seen.append(str(prompt))
                return AIMessage(content="Verified news analysis marker")

            return RunnableLambda(news)

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
        selected or ["fundamentals"], config=config, research_snapshot=snapshot,
        macro_context=macro_context, seed_reports=seed_reports, market_environment=market_environment,
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
    assert (
        len(calls) == 11
    )  # one constructor + info + six tables + insiders, peer constructor + info
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


def test_existing_news_analyst_joins_the_web_research_graph(monkeypatch, tmp_path, snapshot):
    import yfinance

    monkeypatch.setattr(
        yfinance, "Ticker", lambda *a: pytest.fail("Duplicate financial data fetch")
    )
    graph, seen = make_graph(
        monkeypatch,
        tmp_path,
        snapshot,
        selected=["fundamentals", "operating", "metrics", "news"],
        allow_news=True,
    )
    state, rating = graph.propagate("AAPL", snapshot["as_of_date"])
    assert state["news_report"].startswith("Verified news analysis marker")
    assert len(seen) == 12
    bull = next(text for text in seen if "You are a Bull Analyst" in text)
    assert "Verified news analysis marker" in bull
    assert "Operating evidence-based conclusion" in bull


def test_macro_evidence_reaches_original_news_and_all_decision_roles_without_financial_refetch(
    monkeypatch, tmp_path, snapshot
):
    import yfinance

    macro = {
        "id": "macro-six-series",
        "status": "completed",
        "as_of_date": snapshot["as_of_date"],
        "retrieved_at": snapshot["as_of_date"] + "T12:00:00+08:00",
        "source_mode": "fred_public_latest",
        "reports": [
            "DGS10: 4.98 percent; observation date 2026-11-10; https://fred.stlouisfed.org/series/DGS10"
        ],
    }
    seeds = {
        "fundamentals_report": "Original fundamentals",
        "operating_report": "Original operations",
        "metrics_report": "Original financial metrics",
    }
    monkeypatch.setattr(yfinance, "Ticker", lambda *a: pytest.fail("Financial data was refetched"))
    graph, seen = make_graph(
        monkeypatch,
        tmp_path,
        snapshot,
        selected=["fundamentals", "operating", "metrics", "news"],
        allow_news=True,
        macro_context=macro,
        seed_reports=seeds,
    )
    macro["reports"][0] = "CHANGED BY CALLER"
    state, rating = graph.propagate("AAPL", snapshot["as_of_date"])
    assert rating == "Hold"
    assert len(seen) == 9  # original news analyst + eight downstream decision roles
    assert all(
        "4.98" in prompt and "macro-six-series" in prompt and "2026-11-10" in prompt
        for prompt in seen
    )
    assert all("CHANGED BY CALLER" not in prompt for prompt in seen)
    assert all(state[key] == value for key, value in seeds.items())
    assert state["macro_context"]["reports"][0].startswith("DGS10: 4.98")
    path = next((tmp_path / "results").rglob("full_states_log_*.json"))
    assert json.loads(path.read_text())["macro_context"] == state["macro_context"]


def test_archived_retry_replays_all_four_analysts_without_current_vendor_data(monkeypatch, tmp_path, snapshot):
    import yfinance

    from tradingagents.graph import trading_graph

    monkeypatch.setattr(yfinance, "Ticker", lambda *a: pytest.fail("No current data in archived replay"))
    monkeypatch.setattr(trading_graph, "is_historical", lambda date: True)
    seeds = {key: "archived " + key for key in (
        "fundamentals_report", "operating_report", "metrics_report", "news_report"
    )}
    graph, seen = make_graph(
        monkeypatch, tmp_path, snapshot,
        selected=["fundamentals", "operating", "metrics", "news"], seed_reports=seeds,
    )
    state, rating = graph.propagate("AAPL", snapshot["as_of_date"])
    assert rating == "Hold" and len(seen) == 8
    for key, report in seeds.items():
        assert state[key] == report


def test_historical_company_graph_uses_same_analysts_without_live_tools(monkeypatch, tmp_path, snapshot):
    import yfinance

    from tradingagents.graph import trading_graph
    from tradingagents.web.point_in_time import NOTICE, VERSION, digest

    monkeypatch.setattr(yfinance, 'Ticker', lambda *a, **kw: pytest.fail('Live vendor accessed'))
    snapshot['as_of_date'] = '2020-03-20'
    snapshot['company']['statements']['annual']['income']['periods'] = ['2019-09-30']
    docs = [{'filed': '2020-01-29', 'period': '2019-12-31', 'excerpts': [{'text': 'Historical business evidence'}]}]
    snapshot['company']['filings'] = {'documents': docs}
    snapshot['peers'] = []
    snapshot['point_in_time'] = {'version': VERSION, 'evidence_digest': digest(docs), 'notice': NOTICE, 'facts': []}
    monkeypatch.setattr(trading_graph, 'is_historical', lambda d: True)
    graph, seen = make_graph(monkeypatch, tmp_path, snapshot,
                            selected=['fundamentals', 'operating', 'metrics', 'news'],
                            seed_reports={'news_report': 'Historical news unavailable; no current news substituted'})
    graph.config.update(trendradar_enabled=False, market_environment_enabled=False)
    state, _ = graph.propagate('AAPL', '2020-03-20')
    assert len(seen) == 11
    assert all(state[k] for k in ('fundamentals_report', 'operating_report', 'metrics_report', 'investment_plan'))
    assert 'Historical business evidence' in seen[0]
    assert 'later outcomes' in seen[-1]
    assert not (tmp_path / 'memory.md').exists()


def test_pause_stops_graph_before_next_model_step(monkeypatch, tmp_path, snapshot):
    from tradingagents.web.server import PauseRequested
    graph, seen = make_graph(monkeypatch, tmp_path, snapshot)
    boundaries = []
    def stop_after_analyst():
        boundaries.append(True)
        if len(boundaries) == 2:
            raise PauseRequested()
    graph.pause_check = stop_after_analyst
    with pytest.raises(PauseRequested):
        graph.propagate('AAPL', snapshot['as_of_date'])
    assert len(seen) == 1
    assert not (tmp_path / 'memory.md').exists()


def test_pause_capable_graph_completes_normally(monkeypatch, tmp_path, snapshot):
    graph, seen = make_graph(monkeypatch, tmp_path, snapshot)
    graph.pause_check = lambda: None
    state, rating = graph.propagate('AAPL', snapshot['as_of_date'])
    assert rating == 'Hold'
    assert state['final_trade_decision']
    assert len(seen) == 9

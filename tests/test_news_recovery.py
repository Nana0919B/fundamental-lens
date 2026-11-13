"""Preserve completed original news-analyst output and avoid repeated failed runs."""

from types import SimpleNamespace
from langchain_core.messages import AIMessage
from tradingagents.graph.analyst_execution import AnalystNodeSpec
from tradingagents.graph.setup import _analyst_graph
from tradingagents.agents.analysts.financial_specialists import create_financial_specialist


def test_completed_report_is_published_before_the_parent_finishes():
    seen = []
    spec = AnalystNodeSpec("news", "News Analyst", "news_report")
    graph = _analyst_graph(
        spec,
        lambda state: {
            "messages": [AIMessage(content="news")],
            "news_report": "original news report",
        },
        1,
        on_report=lambda key, text: seen.append((key, text)),
    )
    result = graph.invoke({"messages": []})
    assert seen == [("news_report", "original news report")]
    assert result["news_report"] == "original news report"


def test_empty_specialist_output_gets_one_explicit_plaintext_retry():
    calls = []

    class LLM:
        def with_structured_output(self, *a):
            raise NotImplementedError

        def invoke(self, prompt):
            calls.append(prompt)
            return AIMessage(content="" if len(calls) == 1 else "## 核心结论\n数据不足以确定原因")

    result = create_financial_specialist(LLM(), "operating")(
        {
            "research_snapshot": {
                "id": "one",
                "as_of_date": "2026-11-13",
                "company": {"profile": {}, "statements": {}},
            }
        }
    )
    assert "数据不足" in result["operating_report"]
    assert len(calls) == 2
    assert "contained no readable text" in str(calls[-1])


def test_yahoo_quote_failure_falls_back_to_search(monkeypatch):
    from tradingagents.dataflows.vendors.yahoo import news
    from tradingagents.dataflows.errors import VendorUnavailableError

    monkeypatch.setattr(news, "_session_options", lambda: {})

    class Ticker:
        def __init__(self, *a):
            pass

        def get_news(self, **k):
            raise VendorUnavailableError("SSL failure")

    monkeypatch.setattr(news.yf, "Ticker", Ticker)
    monkeypatch.setattr(
        news,
        "_search_news",
        lambda *a: (
            [
                {
                    "title": "MSFT source",
                    "publisher": "Source",
                    "link": "https://example.com/msft",
                    "providerPublishTime": 1794560400,
                }
            ],
            True,
        ),
    )
    result = news.get_news_yfinance("MSFT", "2026-11-12", "2026-11-14")
    assert "MSFT source" in result
    assert "https://example.com/msft" in result


def test_configured_ca_is_passed_to_curl_instead_of_disabling_tls(monkeypatch, tmp_path):
    from tradingagents.dataflows.vendors.yahoo import news
    from curl_cffi import requests

    cert = tmp_path / "ca.pem"
    cert.write_text("test")
    captured = {}
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert))
    monkeypatch.setattr(
        requests, "Session", lambda **kwargs: captured.update(kwargs) or "verified-session"
    )
    assert news._session_options() == {"session": "verified-session"}
    assert captured["verify"] == str(cert)


def test_repair_reuses_archived_financial_data_and_persists_early_news(monkeypatch, tmp_path):
    from tradingagents.web import server
    from tradingagents.graph import trading_graph
    from tradingagents.dataflows.research_analytics import enrich_snapshot

    snapshot = enrich_snapshot(
        {
            "id": "repair",
            "as_of_date": "2026-11-13",
            "company": {"symbol": "MSFT", "profile": {}, "statements": {}},
            "peers": [],
        }
    )
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(
        server,
        "JOBS",
        {
            "repair": {
                "id": "repair",
                "ticker": "MSFT",
                "date": "2026-11-13",
                "status": "failed",
                "research_snapshot": snapshot,
            }
        },
    )
    monkeypatch.setattr(
        server,
        "company_data",
        lambda *a: (_ for _ in ()).throw(AssertionError("Refetched financials")),
    )

    class Graph:
        def __init__(self, **kwargs):
            self.publish = kwargs["on_report"]

        def propagate(self, *args):
            self.publish("news_report", "Original news analyst report")
            assert server.JOBS["repair"]["reports"]["news_report"] == "Original news analyst report"
            raise RuntimeError("Another analyst failed")

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    server.run_job("repair")
    assert server.JOBS["repair"]["status"] == "failed"
    assert server.JOBS["repair"]["reports"]["news_report"] == "Original news analyst report"
    assert server.JOBS["repair"]["research_snapshot"] == snapshot


def test_model_excerpt_preserves_original_evidence_positions():
    import json
    from tradingagents.agents.financial_snapshot import snapshot_context

    snapshot = {
        "id": "one",
        "as_of_date": "2026-11-13",
        "company": {
            "profile": {},
            "statements": {
                "annual": {
                    "income": {
                        "periods": ["2025-12-31"],
                        "rows": [
                            {"name": "Unused Item", "values": [10]},
                            {"name": "Total Revenue", "values": [100]},
                        ],
                    }
                }
            },
        },
    }
    text = snapshot_context(snapshot, full=True)
    assert "annual.income.1.0" in text
    assert "Unused Item" not in text
    assert "100" in text


def test_original_news_analyst_retries_empty_output_and_keeps_tool_sources(monkeypatch):
    from tradingagents.agents.analysts import news_analyst
    from langchain_core.messages import ToolMessage

    calls = []

    def turn(*args):
        calls.append(args)
        text = "" if len(calls) == 1 else "MSFT 新闻分析"
        return AIMessage(content=text), text

    monkeypatch.setattr(news_analyst, "take_turn", turn)
    report = news_analyst.create_news_analyst(None)(
        {
            "company_of_interest": "MSFT",
            "trade_date": "2026-11-13",
            "messages": [
                ToolMessage(
                    content="Link: https://example.com/msft", tool_call_id="one", name="get_news"
                )
            ],
        }
    )["news_report"]
    assert len(calls) == 2
    assert "MSFT 新闻分析" in report
    assert "https://example.com/msft" in report
    assert "新闻工具返回的原始来源" in report

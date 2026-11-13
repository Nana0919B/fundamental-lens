"""Public FRED observations must not introduce historical look-ahead."""

from types import SimpleNamespace
import pytest
from tradingagents.dataflows.vendors import fred


def test_current_analysis_can_use_official_csv_without_a_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "get_current_date", lambda: "2026-11-13")

    def public_response(url, *, params, timeout, secret):
        assert secret == ""
        assert "api_key" not in params
        return SimpleNamespace(
            text="observation_date,DGS10\n2026-11-09,4.88\n2026-11-10,4.98\n2026-11-11,.\n2026-11-14,9\n"
        )

    monkeypatch.setattr(fred, "get_scrubbed", public_response)
    out = fred.get_macro_data("10y_treasury", "2026-11-13", 30)
    assert "4.98" in out and "2026-11-10" in out
    assert "最新修订值" in out and "无需 API Key" in out
    assert "2026-11-14" not in out
    assert "https://fred.stlouisfed.org/series/DGS10" in out


def test_public_csv_is_not_used_for_historical_backtests(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "get_current_date", lambda: "2026-11-13")
    monkeypatch.setattr(fred, "get_scrubbed", lambda *a, **k: pytest.fail("Historical CSV request"))
    with pytest.raises(fred.FredNotConfiguredError, match="历史时点"):
        fred.get_macro_data("cpi", "2025-09-30")


def test_invalid_or_unexpected_csv_is_not_treated_as_data(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "get_current_date", lambda: "2026-11-13")
    monkeypatch.setattr(
        fred, "get_scrubbed", lambda *a, **k: SimpleNamespace(text="<html>Error</html>")
    )
    with pytest.raises(fred.VendorUnavailableError):
        fred.get_macro_data("cpi", "2026-11-13")


def test_msft_news_macro_supplement_preserves_original_and_shows_real_data():
    from tradingagents.web.server import display_reports

    original = "MSFT 新闻\n本次运行中 FRED 未配置，get_macro_indicators 不可用\n公司事件分析"
    job = {
        "reports": {"news_report": original},
        "macro_update": {
            "status": "completed",
            "retrieved_at": "2026-11-13T18:00:00+08:00",
            "reports": ["## FRED：美债收益率\n最新读数：4.98（2026-11-10）"],
        },
    }
    shown = display_reports(job)["news_report"]
    assert "FRED 未配置" not in shown
    assert "4.98" in shown and "2026-11-10" in shown
    assert "不是原报告时点的历史版本" in shown
    assert "公司事件分析" in shown
    assert job["reports"]["news_report"] == original

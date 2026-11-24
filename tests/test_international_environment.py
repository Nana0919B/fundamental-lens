"""Market routing, local-session cutoffs, transparent scores and immutable evidence."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingagents.dataflows.market_environment import (
    build_market_environment,
    market_environment_report,
    market_environment_text,
    validate_market_environment,
)
from tradingagents.dataflows.market_environment_global import market_for_ticker

DAY = "2026-11-23"
NOW = datetime(2026, 11, 23, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
CONFIG = {
    "market_environment_enabled": True,
    "market_environment_timeout": 3,
    "market_environment_max_age_days": 7,
}


def prices(end="2026-11-20", slope=1):
    dates = pd.bdate_range(end=end, periods=120)
    return pd.DataFrame({"Date": dates, "Close": [100 + slope * i * 0.2 for i in range(120)]})


def source(monkeypatch, frames=None):
    from tradingagents.dataflows import market_environment_global as module

    calls = []

    class Client:
        def __init__(self, *, timeout):
            assert timeout == 3

        def history(self, symbol, cutoff):
            calls.append((symbol, cutoff))
            value = (frames or {}).get(symbol, prices())
            if isinstance(value, Exception):
                raise module.FutuDataError("源数据不可用")
            return value.copy()

    monkeypatch.setattr(module, "FutuQuoteClient", Client)
    return calls


@pytest.mark.parametrize(
    "ticker,market",
    [
        ("600519.SS", "CN_A"),
        ("000001.SZ", "CN_A"),
        ("0700.HK", "HK"),
        ("AAPL", "US"),
        ("BRK-B", "US"),
        ("BTC-USD", None),
        ("GC=F", None),
        ("VOD.L", None),
    ],
)
def test_market_routing(ticker, market):
    assert market_for_ticker(ticker) == market


@pytest.mark.parametrize(
    "ticker,market,primary", [("AAPL", "US", "US.SPY"), ("0700.HK", "HK", "HK.02800")]
)
def test_build_preserves_observed_metrics_and_market_scope(monkeypatch, ticker, market, primary):
    calls = source(monkeypatch)
    context = build_market_environment(CONFIG, ticker, DAY, now=NOW)
    validate_market_environment(context, ticker, DAY)
    assert context["market"] == market and context["source"] == "Futu OpenAPI"
    assert context["primary_symbol"] == primary
    assert context["latest"]["date"] == "2026-11-20"
    assert context["latest"]["score"] is not None
    assert context["latest"]["realized_vol_20d"] >= 0
    assert context["latest"]["breadth_sample_size"] == len(context["reference_universe"])
    assert context["history"] and context["references"]
    assert context["methodology"]["version"] == "international_daily_v1"
    assert not {"limit_up", "seal_rate", "phase"} & context["latest"].keys()
    assert len(calls) == len(context["reference_universe"])
    assert context["coverage"]["status"] == "available"
    text = market_environment_text(context)
    assert market in text and "reference basket" in text and "not all listed stocks" in text
    assert "SUPPLIED A-SHARE" not in text
    assert "Futu OpenAPI" in market_environment_report(context)
    assert "封板" not in market_environment_report(context)
    original = deepcopy(context)
    context["latest"]["score"] = 0
    with pytest.raises(ValueError):
        validate_market_environment(context, ticker, DAY)
    validate_market_environment(original, ticker, DAY)


@pytest.mark.parametrize(
    "stamp,market,cutoff",
    [
        ("2026-11-23T15:00:00+08:00", "HK", "2026-11-22"),
        ("2026-11-23T17:00:00+08:00", "HK", "2026-11-23"),
        ("2026-11-24T04:00:00+08:00", "US", "2026-11-22"),
        ("2026-11-24T06:00:00+08:00", "US", "2026-11-23"),
        ("2026-07-15T04:00:00+08:00", "US", "2026-07-13"),
        ("2026-07-15T05:00:00+08:00", "US", "2026-07-14"),
    ],
)
def test_market_timezone_and_dst_cutoffs(monkeypatch, stamp, market, cutoff):
    calls = source(monkeypatch)
    now = datetime.fromisoformat(stamp)
    context = build_market_environment(
        CONFIG, "AAPL" if market == "US" else "0700.HK", now.date().isoformat(), now=now
    )
    assert context["cutoff_date"] == cutoff
    assert all(c[1] == cutoff for c in calls)


def test_future_rows_unaligned_and_missing_references(monkeypatch):
    calls = source(
        monkeypatch,
        {
            "US.SPY": prices("2026-11-24"),
            "US.RSP": RuntimeError("secret-url"),
            "US.IWM": prices("2026-11-19"),
        },
    )
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    assert c["latest"]["date"] <= c["cutoff_date"]
    assert c["coverage"]["status"] == "partial"
    assert c["latest"]["breadth_sample_size"] < len(c["reference_universe"])
    assert all(r["date"] == c["latest"]["date"] for r in c["references"])
    assert "secret-url" not in str(c)
    assert calls


def test_missing_primary_short_history_and_single_reference_do_not_fabricate_scores(monkeypatch):
    source(monkeypatch, {"US.SPY": RuntimeError("unavailable")})
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    assert c["coverage"]["status"] == "unavailable" and c["latest"] is None
    source(monkeypatch, {"US.SPY": prices().tail(15)})
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    assert c["latest"] is None
    source(monkeypatch, {s: RuntimeError("missing") for s in ("US.QQQ", "US.IWM", "US.RSP")})
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    assert c["latest"]["score"] is None and c["latest"]["state"] is None
    assert c["latest"]["breadth_ratio"] is None


def test_stale_historical_disabled_and_legacy_retry(monkeypatch):
    calls = source(monkeypatch, {"US.SPY": prices("2026-11-01")})
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    assert c["coverage"]["status"] == "stale"
    calls.clear()
    for ticker in ("AAPL", "0700.HK"):
        c = build_market_environment(CONFIG, ticker, "2026-11-20", now=NOW)
        assert c["coverage"]["status"] == "unavailable"
        c = build_market_environment(CONFIG, ticker, DAY, now=NOW, allow_fetch=False)
        assert c["latest"] is None
    assert not calls
    assert (
        build_market_environment(
            {**CONFIG, "market_environment_enabled": False}, "AAPL", DAY, now=NOW
        )
        is None
    )


@pytest.mark.parametrize("ticker", ["AAPL", "0700.HK"])
def test_real_graph_archives_and_passes_futu_evidence_to_research_and_risk(
    monkeypatch, tmp_path, ticker
):
    import json

    from tests.test_financial_snapshot import make_graph
    from tradingagents.dataflows.date_window import get_current_date
    from tradingagents.dataflows.research_store import ResearchStore
    from tradingagents.reporting import write_report_tree

    day = get_current_date()
    now = datetime.fromisoformat(day + "T18:00:00+08:00")
    calls = source(monkeypatch)
    context = build_market_environment(CONFIG, ticker, day, now=now)
    snapshot = {
        "id": "futu-" + ticker,
        "as_of_date": day,
        "company": {"symbol": ticker, "profile": {}, "statements": {}},
        "peers": [],
    }
    original = deepcopy(context)
    graph, prompts = make_graph(monkeypatch, tmp_path, snapshot, market_environment=context)
    context["references"][0]["close"] = 1
    monkeypatch.setattr(
        "tradingagents.dataflows.market_environment_global.FutuQuoteClient",
        lambda **kw: pytest.fail("Frozen graph tried to refetch"),
    )
    state, _ = graph.propagate(ticker, day)
    assert len(prompts) == 9
    assert all(original["snapshot_id"] in p and "Futu OpenAPI" in p for p in prompts)
    assert all("reference-basket breadth" in p for p in prompts)
    assert state["market_environment"] == original
    archive = ResearchStore(tmp_path / "cache/environment.sqlite3")
    assert archive.load_market_environment(original["snapshot_id"]) == original
    path = next((tmp_path / "results").rglob("full_states_log_*.json"))
    assert json.loads(path.read_text())["market_environment"] == original
    write_report_tree(state, ticker, tmp_path / "export")
    exported = (tmp_path / "export/complete_report.md").read_text()
    assert "Futu OpenAPI" in exported and original["market_name"] in exported
    assert calls


def test_numeric_metrics_match_independent_fixed_sample(monkeypatch):
    dates = pd.bdate_range(end="2026-11-20", periods=100)
    frame = pd.DataFrame({"Date": dates, "Close": [float(i) for i in range(1, 101)]})
    source(monkeypatch, dict.fromkeys(("US.SPY", "US.QQQ", "US.IWM", "US.RSP"), frame))
    c = build_market_environment(CONFIG, "AAPL", DAY, now=NOW)
    row = c["latest"]
    assert row["close"] == 100
    assert row["ma20"] == 90.5 and row["ma60"] == 70.5
    assert row["return_20d"] == 0.25 and row["drawdown_60d"] == 0
    # Independent calculation of the last 20 simple returns, not price-level std.
    returns = [i / (i - 1) - 1 for i in range(81, 101)]
    mean = sum(returns) / 20
    volatility = (sum((x - mean) ** 2 for x in returns) / 19) ** 0.5 * 252**0.5
    assert row["realized_vol_20d"] == pytest.approx(volatility, abs=1e-6)
    assert row["breadth_ratio"] == 1 and row["trend_score"] == 100
    expected = round(40 + 30 + 20 + 0.1 * max(0, min(100, 100 - 200 * volatility)))
    assert row["score"] == expected


def test_authorized_posthoc_refresh_is_labelled_and_cannot_enter_original_research(monkeypatch):
    from tradingagents.dataflows.market_environment_global import build_global_environment

    calls = source(monkeypatch, {s: prices('2026-11-13') for s in ('US.SPY','US.QQQ','US.IWM','US.RSP')})
    c = build_global_environment(CONFIG, 'MSFT', '2026-11-13', market='US', now=NOW, posthoc=True)
    assert c['latest']['date'] == '2026-11-13'
    assert c['source_mode'] == 'posthoc_latest_revision'
    assert '未重新生成' in c['refresh_note']
    assert calls and all(call[1] == '2026-11-13' for call in calls)
    validate_market_environment(c, 'MSFT', '2026-11-13')
    with pytest.raises(ValueError, match='Posthoc'):
        validate_market_environment(c, 'MSFT', '2026-11-13', for_research=True)
    assert '未重新生成' in market_environment_report(c)

"""Financial calculation boundaries and immutable evidence/version storage."""

from copy import deepcopy
import pytest

from tradingagents.dataflows.research_analytics import (
    enrich_snapshot,
    metric_history,
    compare_peers,
)
from tradingagents.dataflows.research_store import ResearchStore


def company():
    def table(periods, rows):
        return {"periods": periods, "rows": [{"name": k, "values": v} for k, v in rows.items()]}

    return {
        "symbol": "AAPL",
        "source": "Yahoo",
        "retrieved_at": "2026-11-13T10:00:00+08:00",
        "profile": {
            "longName": "Apple",
            "financialCurrency": "USD",
            "industry": "Hardware",
            "mostRecentQuarter": 123,
            "trailingPE": 20.0,
        },
        "statements": {
            "annual": {
                "income": table(
                    ["2025-12-31", "2024-12-31"],
                    {"Total Revenue": [200, 100], "Net Income": [20, 5], "Gross Profit": [80, 30]},
                ),
                "balance": table(
                    ["2025-12-31", "2024-12-31"],
                    {
                        "Total Assets": [300, 200],
                        "Stockholders Equity": [100, 60],
                        "Current Assets": [80, 60],
                        "Current Liabilities": [40, 30],
                    },
                ),
                "cashflow": table(
                    ["2025-12-31", "2024-12-31"],
                    {"Operating Cash Flow": [30, 10], "Capital Expenditure": [-10, -5]},
                ),
            },
            "quarterly": {
                "income": table(
                    ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"],
                    {"Total Revenue": [60, 50, 40, 30, 25], "Net Income": [6, 5, 4, 3, 2]},
                )
            },
        },
    }


def test_average_balances_growth_and_ttm():
    result = metric_history(company())
    row = result["history"][0]["values"]
    assert row["roe"] == 0.25  # 20 / average(100,60), not closing balance
    assert row["roa"] == 0.08
    assert row["gross_margin"] == 0.4
    assert row["free_cashflow"] == 20
    assert result["ttm"]["values"]["revenue"] == 180
    assert result["ttm"]["values"]["net_income"] == 18
    assert "operating_cashflow" not in result["ttm"]["values"]
    annual = next(
        g for g in result["growth"] if g["field"] == "revenue" and g["frequency"] == "annual"
    )
    assert annual["value"] == 1
    qoq = next(g for g in result["growth"] if g["basis"] == "QoQ")
    assert qoq["value"] == 0.2
    yoy = next(g for g in result["growth"] if g["frequency"] == "quarterly" and g["basis"] == "YoY")
    assert yoy["value"] == 1.4


def test_missing_quarters_negative_equity_and_capex_are_not_silently_fixed():
    data = company()
    data["statements"]["quarterly"]["income"]["periods"][1] = "2025-03-31"
    data["statements"]["annual"]["balance"]["rows"][1]["values"][0] = -100
    data["statements"]["annual"]["cashflow"]["rows"][1]["values"][0] = 10
    result = metric_history(data)
    assert result["ttm"]["values"]["revenue"] is None
    assert result["history"][0]["values"]["roe"] is None
    assert result["history"][0]["values"]["free_cashflow"] is None


def test_selected_sample_percentiles_require_aligned_industry_and_period():
    peers = [
        {"symbol": "B", "industry": "Hardware", "mostRecentQuarter": 123, "trailingPE": 10},
        {"symbol": "C", "industry": "Hardware", "mostRecentQuarter": 123, "trailingPE": 30},
        {"symbol": "D", "industry": "Bank", "mostRecentQuarter": 123, "trailingPE": 5},
        {"symbol": "E", "industry": "Hardware", "mostRecentQuarter": 456, "trailingPE": 5},
    ]
    result = compare_peers(company(), peers)
    pe = next(d for d in result["distributions"] if d["metric"] == "trailingPE")
    assert pe["sample_size"] == 3
    assert pe["percentile"] == pytest.approx(100 / 3)
    assert result["rows"][3]["same_industry"] is False
    assert result["rows"][4]["period_aligned"] is False
    assert compare_peers(company(), [])["distributions"][0]["percentile"] is None


def test_snapshot_facts_and_reports_are_append_only(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    snap = enrich_snapshot(
        {"id": "one", "as_of_date": "2026-11-13", "company": company(), "peers": []}
    )
    digest = store.archive_snapshot(snap)
    assert store.archive_snapshot(snap) == digest
    assert store.history("AAPL")[0]["has_report"] == 0
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert count == len(snap["evidence"])
    assert all(
        ref in {e["id"] for e in snap["evidence"]}
        for h in snap["analytics"]["history"]
        for ref in h["evidence_refs"]
    )
    store.archive_reports(snap, {"brief": "first"})
    assert store.history("AAPL")[0]["has_report"] == 1
    with pytest.raises(ValueError):
        store.archive_reports(snap, {"brief": "modified"})
    changed = deepcopy(snap)
    changed["company"]["profile"]["longName"] = "Changed"
    with pytest.raises(ValueError):
        store.archive_snapshot(changed)
    changed["id"] = "two"
    changed["company"]["retrieved_at"] = "2026-11-13T11:00:00+08:00"
    assert store.profile_changes(changed)["changes"][0]["field"] == "longName"
    store.archive_snapshot(changed)
    assert len(store.history("AAPL")) == 2
    store.save_peers("AAPL", ["B", "C"])
    assert store.peer_pool("AAPL") == ["B", "C"]


def test_company_brief_reuses_specialist_conclusions_without_another_model():
    from tradingagents.report_brief import make_company_brief

    snapshot = {"id": "version-one", "company": company(), "analytics": {"formula_version": "1.0"}}
    reports = {
        "operating_report": "## 核心结论\n经营增长受限\n## 风险提示\n现金转换待验证\n## 后续核查问题\n核查库存",
        "metrics_report": "## 核心结论\n估值需结合盈利\n## 风险提示\n杠杆较高\n## 后续核查问题\n核查负债期限",
    }
    brief = make_company_brief(snapshot, reports)
    for text in ("经营增长受限", "现金转换待验证", "估值需结合盈利", "杠杆较高", "version-one"):
        assert text in brief
    assert "未找到独立章节" not in brief


def test_company_brief_accepts_plaintext_and_numbered_model_headings():
    from tradingagents.report_brief import make_company_brief

    snapshot = {"id": "one", "company": company()}
    reports = {
        "operating_report": "核心结论\n经营结论\n事实与证据\n证据\n风险提示\n经营风险\n后续核查问题\n经营核查",
        "metrics_report": "## 一、核心结论\n财务结论\n## 二、风险提示\n财务风险\n## 三、后续核查问题\n财务核查",
    }
    brief = make_company_brief(snapshot, reports)
    assert "未找到独立章节" not in brief
    assert all(
        value in brief
        for value in ("经营结论", "财务结论", "经营风险", "财务风险", "经营核查", "财务核查")
    )

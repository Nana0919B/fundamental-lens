"""Presentation is understandable while archived evidence remains unmodified."""

from tradingagents.web.presentation import present_report, readable_evidence


def fixture():
    return {
        "id": "0123456789abcdef0123456789abcdef",
        "company": {"profile": {"financialCurrency": "USD"}},
        "evidence": [
            {
                "id": "profile.returnOnEquity",
                "field": "returnOnEquity",
                "value": 0.25,
                "period": "current snapshot",
                "source": "Yahoo",
            },
            {
                "id": "annual.income.0.0",
                "field": "Total Revenue",
                "value": 1000000,
                "period": "2025-12-31",
                "frequency": "annual",
                "currency": "USD",
            },
        ],
    }


def test_evidence_explains_metric_period_value_and_units():
    rows = readable_evidence(fixture())
    assert rows[0]["label"] == "净资产收益率 ROE"
    assert rows[0]["display_value"] == "25.00%"
    assert "杠杆" in rows[0]["explanation"]
    assert rows[1]["period"] == "年度 · 2025-12-31"
    assert "USD" in rows[1]["display_value"]


def test_old_reports_hide_trace_ids_and_citations_become_readable():
    snapshot = fixture()
    raw = f"快照编号：{snapshot['id']}；来源：Yahoo\nTradingAgents\n[profile.returnOnEquity](#evidence-profile.returnOnEquity)"
    output = present_report(raw, snapshot)
    assert snapshot["id"] not in output
    assert "快照编号" not in output
    assert "FundAnalyst" in output and "TradingAgents" not in output
    assert "[净资产收益率 ROE · 当前资料（采集时点） · 25.00%]" in output
    assert "(#evidence-profile.returnOnEquity)" in output
    assert snapshot["id"] in raw  # the archived version was not rewritten


def test_framework_urls_are_preserved_and_unknown_references_are_not_technical_labels():
    output = present_report(
        "[source](https://github.com/TauricResearch/TradingAgents)\n[unknown](#evidence-missing)"
    )
    assert "https://github.com/TauricResearch/TradingAgents" in output
    assert "[查看财务依据]" in output

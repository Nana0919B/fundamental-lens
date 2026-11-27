from copy import deepcopy

import pytest

from tradingagents.web import valuation
from tradingagents.web.backtest_hk import catalog, convert
from tradingagents.web.historical_valuation import research_job
from tradingagents.web.valuation_backtest import historical_value
from tradingagents.web.valuation_policy import build


def history(ticker="0700.HK"):
    s = deepcopy(catalog()[ticker]["snapshots"][0])
    s.update(minimum_history=1, currency="HKD", fx_rows=[{"date": "2025-05-29", "close": 1.1}])
    return s


def job(ticker="0700.HK"):
    s = convert([history(ticker)], "2025-05-30")
    return research_job(s, "2025-05-30", s[0]["shares"], 100, ticker)


def test_policy_does_not_read_price_or_trading_outcomes():
    company = job()["research_snapshot"]["company"]
    first = build(company, "0700.HK")
    changed = deepcopy(company)
    changed["profile"]["currentPrice"] = 1e9
    changed["trades"] = [{"return": 1000}]
    assert build(changed, "0700.HK") == first
    assert first["audit"]["periods"] == ["2024-12-31", "2023-12-31"]
    assert first["scenarios"]["base"]["margins"][-1] > 0.20
    assert first["scenarios"]["base"]["growth"][0] != first["scenarios"]["base"]["growth"][-1]


def test_comparative_columns_not_backdated():
    s = history()
    with pytest.raises(ValueError, match="尚未公开"):
        research_job([s], s["filed"], s["shares"], 100, "0700.HK")
    late = job()
    late["historical_context"]["source_ledger"][0]["facts"]["previous_revenue"]["filed"] = (
        "2025-05-31"
    )
    with pytest.raises(ValueError, match="截止日"):
        valuation.prepare(late)


def test_terminal_growth_funded_by_reinvestment_once():
    j = job()
    p = valuation.prepare(j)["inputs"]
    p.update(
        revenue=100,
        cash=0,
        debt=0,
        operating_cash=0,
        non_operating_assets=0,
        other_claims=0,
        shares=1,
    )
    s = {
        **p["scenarios"]["base"],
        "growth": [0] * 5,
        "margins": [0.20] * 5,
        "tax_rate": 0.25,
        "da_ratio": 0.03,
        "capex_ratio": 0.03,
        "nwc_ratio": 0,
        "terminal_growth": 0.02,
        "terminal_roic": 0.10,
        "wacc": 0.10,
    }
    r = valuation.project(p, s)
    assert r["terminal_reinvestment"] == pytest.approx(100 * 1.02 * 0.2 * 0.75 * 0.02 / 0.10)
    assert r["terminal_fcff"] == pytest.approx(100 * 1.02 * 0.2 * 0.75 * 0.8)
    assert r["enterprise_value"] == pytest.approx(
        sum(15 / 1.1**i for i in range(1, 6)) + 15.3 * 0.8 / 0.08 / 1.1**5
    )
    with pytest.raises(ValueError, match="ROIC"):
        bad = deepcopy(p)
        bad["scenarios"]["base"]["terminal_roic"] = 0.01
        valuation.validate(bad)


def test_separate_asset_bridge_no_cash_double_counting():
    j = job()
    p = valuation.prepare(j)["inputs"]
    s = p["scenarios"]["bear"]
    r = valuation.project(p, s)
    assert r["non_operating_asset_value"] == pytest.approx(p["non_operating_assets"] * 0.7)
    assert r["equity_value"] == pytest.approx(
        r["enterprise_value"]
        + p["cash"]
        - p["operating_cash"]
        + r["non_operating_asset_value"]
        - p["debt"]
        - p["other_claims"]
    )
    assert p["cash"] == pytest.approx((132519 + 77601 + 192977) * 1e6 * 1.1)
    assert p["operating_cash"] > 0
    p["non_operating_assets"] += 1
    with pytest.raises(ValueError, match="non_operating_assets"):
        valuation.calculate(j, p)


def test_byd_placement_cash_and_shares_then_split():
    s = history("1211.HK")
    rows = [
        {"date": "2025-05-29", "close": 100, "split": 0},
        {"date": "2025-05-30", "close": 100, "split": 0},
        {"date": "2025-06-10", "close": 100, "split": 3},
    ]
    value = historical_value([s], rows, "2025-05-30", "1211.HK")
    assert "error" not in value
    assert value["native_shares"] == 3039065855
    assert value["research_job"]["valuation"]["inputs"]["cash"] == pytest.approx(
        102256542e3 * 1.1 + 43383e6
    )
    source = convert([s], "2025-05-30")[0]["evidence"]
    assert source["placement_cash"]["val"] == 43383e6  # already HKD, no double FX
    assert source["placement_shares"]["val"] == 129800000
    s["fx_rows"].append({"date": "2025-06-09", "close": 1.1})
    after = historical_value([s], rows, "2025-06-10", "1211.HK")
    assert after["native_shares"] == 3039065855 * 3


def test_restricted_deposits_excluded():
    p = valuation.prepare(job("0941.HK"))["inputs"]
    assert p["cash"] == pytest.approx((167309 + 118029) * 1e6 * 1.1)
    assert p["cash"] != pytest.approx((167309 + 129379) * 1e6 * 1.1)


def test_unmapped_company_is_not_labeled_as_industry_policy():
    study = job()
    study['ticker'] = 'GOOGL'
    prepared = valuation.prepare(study)
    assert prepared['policy_audit'] is None
    assert prepared['inputs']['assumption_policy'] == 'generic-defaults-1.0'
    assert any('尚未匹配行业政策' in w for w in prepared['warnings'])

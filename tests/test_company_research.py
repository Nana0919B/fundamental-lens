"""Company facts are date/report bound; cash, securities and conversion reconcile."""

import hashlib
import json
from copy import deepcopy

import pytest

from tradingagents.web import company_research as cr, valuation as v


@pytest.fixture
def researched(monkeypatch, tmp_path):
    plan = json.loads(cr.PLAN.read_text())
    reports = {
        "operating_report": "核实分部与资本开支",
        "fundamentals_report": "核实股数、优先股与投资",
    }
    plan["report_sha256"] = {k: hashlib.sha256(t.encode()).hexdigest() for k, t in reports.items()}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(cr, "PLAN", path)
    job = {
        "id": plan["job_id"],
        "ticker": "GOOGL",
        "date": plan["as_of_date"],
        "snapshot_digest": plan["snapshot_digest"],
        "reports": reports,
    }
    return job, cr.build(job)["inputs"]


def test_all_common_classes_and_no_cash_investment_double_count(researched):
    _, p = researched
    assert p["shares"] == (5888 + 827 + 5579) * 1e6
    assert p["cash"] == (265098 - 90384) * 1e6
    assert p["non_operating_assets"] == (90384 + 135230) * 1e6
    assert p["debt"] == (125102 + 1016 + 3705) * 1e6
    assert p["other_claims"] == (6900 + 27833 - 1458 + 12219) * 1e6
    assert p["research_bridge"]["conversion_shares"] == pytest.approx(
        385e6 / 20 / 2 * (2.816 + 2.842)
    )


def test_date_and_report_guard(researched):
    job, _ = researched
    for k, value in [("date", "2020-03-23"), ("snapshot_digest", "changed"), ("reports", {})]:
        bad = deepcopy(job)
        bad[k] = value
        with pytest.raises(ValueError):
            cr.build(bad)
    bad = deepcopy(job)
    bad["id"] = "another-study"
    assert cr.build(bad) is None


def test_business_forecast_does_not_read_share_price(researched):
    job, p = researched
    job["data"] = {"profile": {"currentPrice": 1000000}}
    assert cr.build(job)["inputs"]["scenarios"] == p["scenarios"]
    for s in p["scenarios"].values():
        assert len(s["growth"]) == p["years"] == 10
        assert s["capex_ratios"][0] > 0.30  # AI buildout is not the generic 3.5%.


def test_stub_preferred_and_common_equity_reconcile(researched):
    _, p = researched
    p = v.validate(p)
    s = p["scenarios"]["base"]
    r = v.project(p, s)
    t = r["research_transition"]
    b = p["research_bridge"]
    assert t["estimated_elapsed_cash"] == pytest.approx(
        (s["quarter_fcff"] + s["quarter_net_interest"]) * 58 / 92
        - b["preferred_paid_since_balance"]
    )
    assert t["remaining_fcff"] == pytest.approx(s["quarter_fcff"] * 34 / 92)
    assert r["enterprise_value"] == pytest.approx(
        sum(x["pv"] for x in r["rows"]) + r["terminal_pv"] + t["remaining_pv"]
    )
    assert r["equity_pool"] == pytest.approx(
        r["enterprise_value"]
        + p["cash"]
        + t["estimated_elapsed_cash"]
        - p["operating_cash"]
        + r["non_operating_asset_value"]
        - p["debt"]
        - p["other_claims"]
        - t["dividend_pv"]
    )
    assert r["equity_value"] == pytest.approx(r["price"] * p["shares"])
    assert r["price"] * r["effective_shares"] == pytest.approx(r["equity_pool"])
    assert r["terminal_fcff"] == pytest.approx(r["rows"][-1]["fcff"] * (1 + s["terminal_growth"]))
    reverse = v.reverse(p, s)
    assert reverse["status"] == "solved"
    assert reverse["reproduced_price"] == pytest.approx(p["market_price"])


def test_annual_capex_uses_path_and_invalid_dates_rejected(researched):
    _, p = researched
    r = v.project(v.validate(p), p["scenarios"]["base"])
    assert r["rows"][0]["capex"] == pytest.approx(240000e6)
    assert r["rows"][1]["capex"] == pytest.approx(255000e6)
    bad = deepcopy(p)
    bad["scenarios"]["base"]["capex_ratios"] = [0.1]
    with pytest.raises(ValueError):
        v.validate(bad)
    bad = deepcopy(p)
    bad["research_bridge"]["valuation_date"] = "2027-01-01"
    with pytest.raises(ValueError):
        v.validate(bad)


def test_shared_entrypoint_and_no_cross_company_replay(researched):
    study, p = researched
    study["data"] = {
        "profile": {
            "totalCash": 1,
            "totalDebt": 1,
            "sharesOutstanding": 1,
            "currency": "USD",
            "financialCurrency": "USD",
        },
        "statements": {
            "annual": {
                "income": {
                    "periods": ["2025-12-31"],
                    "rows": [
                        {"name": "Total Revenue", "values": [100]},
                        {"name": "Operating Income", "values": [20]},
                    ],
                }
            }
        },
    }
    result = v.calculate(study, p)
    assert result["sensitivity"]["prices"][2][2] == pytest.approx(
        result["scenarios"]["base"]["price"]
    )
    assert result["inputs"]["research_basis"]["version"] == "googl-research-1.0"
    other = deepcopy(study)
    other["id"] = "other"
    with pytest.raises(ValueError, match="不可套用"):
        v.calculate(other, p)

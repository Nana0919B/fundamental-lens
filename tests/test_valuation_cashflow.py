"""Independent checks of dated transition cash, conversion and cash-flow identity."""

from copy import deepcopy
from datetime import date

import pytest

from tests.test_valuation import fixture_job
from tradingagents.web.valuation import calculate
from tradingagents.web.valuation_cashflow import (
    cashflow_discount,
    forecast_cashflow,
    validate_cashflow,
)


def parameters():
    scenario = {
        "company_fcf": [100, 100, 100],
        "interest": [10, 10, 10],
        "interest_income": [2, 2, 2],
        "equity_comp": [4, 4, 4],
        "interest_tax_rate": [0.25, 0.25, 0.25],
        "wacc": 0.1,
        "terminal_growth": 0,
        "preferred_discount_rate": 0.05,
        "fy_company_fcf": 60,
        "stub_interest": 4,
        "stub_interest_income": 1,
        "stub_equity_comp": 2,
        "stub_tax_rate": 0,
        "terminal_normalization": 5,
    }
    return {
        "method": "company_fcf_bridge",
        "valuation_date": "2026-11-26",
        "balance_date": "2026-09-30",
        "stub_end": "2026-12-31",
        "years": 3,
        "shares": 10,
        "market_price": 50,
        "cash": 30,
        "debt": 20,
        "operating_cash": 5,
        "other_claims": 2,
        "paid_preferred_dividends": 1.5,
        "ytd_company_fcf": 20,
        "rationale": "独立测试：公司现金流调整、剩余期间及优先股正常强制转股。",
        "preferred": {
            "count": 1,
            "liquidation_per_share": 100,
            "coupon": 0.06,
            "min_ratio": 1.5,
            "max_ratio": 2,
            "selected_ratio": 2,
            "conversion_date": "2027-10-15",
            "dividend_dates": ["2027-01-15", "2027-04-15", "2027-07-15", "2027-10-15"],
        },
        "scenarios": {name: deepcopy(scenario) for name in ["bear", "base", "bull"]},
    }


def test_transition_excludes_past_cash_and_rolls_only_net_cash():
    p = validate_cashflow(parameters())
    s = p["scenarios"]["base"]
    r = forecast_cashflow(p, s)
    bridge = r["transition"]
    assert bridge["q4_company_fcf"] == 40
    assert bridge["estimated_elapsed_fcf"] == pytest.approx(40 * 57 / 92)
    assert bridge["rolled_cash"] == pytest.approx(30 + 40 * 57 / 92 - 1.5)
    assert bridge["remaining_fcff"] == pytest.approx((40 + 4 - 1 - 2) * 35 / 92)
    # 2026-12-13 is the day midpoint used for the residual 35-day stub.
    assert bridge["remaining_pv"] == pytest.approx((41 * 35 / 92) / (1.1 ** (17 / 365)))


def test_fcff_not_company_fcf_and_conversion_no_principal_deduction():
    p = validate_cashflow(parameters())
    r = forecast_cashflow(p, p["scenarios"]["base"])
    assert r["rows"][0]["fcff"] == 102  # 100 + (10-2)*.75 - 4
    assert r["terminal_fcff"] == 97  # exclude temporary working capital release of 5
    assert r["effective_shares"] == 12
    expected_pool = (
        r["enterprise_value"]
        + r["transition"]["rolled_cash"]
        - 5
        - 20
        - 2
        - r["preferred_dividend_pv"]
    )
    assert r["equity_pool"] == pytest.approx(expected_pool)
    assert r["price"] == pytest.approx(expected_pool / 12)
    assert r["equity_value"] == pytest.approx(r["price"] * 10)
    assert r["equity_value"] + r["converted_preferred_equity_value"] == pytest.approx(expected_pool)
    # Liquidation preference is not subtracted from the converted equity pool.
    assert r["equity_pool"] != pytest.approx(expected_pool - 100)


def test_preferred_dividends_use_contract_dates_and_own_discount_rate():
    p = validate_cashflow(parameters())
    s = p["scenarios"]["base"]
    r = forecast_cashflow(p, s)
    expected = sum(
        1.5 / (1.05 ** ((date.fromisoformat(d) - date(2026, 11, 26)).days / 365))
        for d in p["preferred"]["dividend_dates"]
    )
    assert r["preferred_dividend_pv"] == pytest.approx(expected)
    changed = forecast_cashflow(p, {**s, "wacc": 0.2})
    assert changed["preferred_dividend_pv"] == pytest.approx(expected)


def test_conversion_boundaries_and_price_order():
    p = validate_cashflow(parameters())
    s = p["scenarios"]["base"]
    r = forecast_cashflow(p, s)
    assert r["conversion_price_range"][0] == pytest.approx(r["price"])
    p["preferred"]["selected_ratio"] = 1.5
    assert forecast_cashflow(p, s)["price"] == pytest.approx(r["conversion_price_range"][1])


def test_tax_shield_curve_has_correct_compounding():
    p = validate_cashflow(parameters())
    s = p["scenarios"]["base"]
    s.update(stub_wacc_adjustment=0.01, wacc_adjustments=[0.01, 0, 0])
    assert cashflow_discount(p, s, date(2027, 12, 31)) == pytest.approx(1.11 ** (400 / 365))
    assert cashflow_discount(p, s, date(2028, 12, 31)) == pytest.approx(
        1.11 ** (400 / 365) * 1.1 ** (366 / 365)
    )


def test_reverse_reproduces_price_and_matrix_center():
    p = parameters()
    p = validate_cashflow(p)
    price = forecast_cashflow(p, p["scenarios"]["base"])["price"]
    p["market_price"] = price
    before = deepcopy(p)
    result = calculate(fixture_job(), p)
    assert result["reverse"]["fcf_multiplier"] == pytest.approx(1)
    assert result["reverse"]["reproduced_price"] == pytest.approx(price)
    assert result["sensitivity"]["prices"][2][2] == pytest.approx(price)
    assert p == before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(valuation_date="2026-09-01"),
        lambda p: p.update(valuation_date="2027-01-01"),
        lambda p: p["preferred"].update(selected_ratio=3),
        lambda p: p["preferred"].update(dividend_dates=["2026-10-15"]),
        lambda p: p["scenarios"]["base"].update(terminal_normalization=200),
        lambda p: p["scenarios"]["base"].update(company_fcf=[True] * 3),
        lambda p: p["scenarios"]["base"].update(wacc_adjustments=[0.1]),
        lambda p: p["scenarios"]["base"].update(wacc=0.02, terminal_growth=0.02),
    ],
)
def test_invalid_or_inconsistent_inputs_fail(mutation):
    p = parameters()
    mutation(p)
    with pytest.raises(ValueError):
        calculate(fixture_job(), p)

"""Mandatory preferred quantities and cash claims must never be double counted."""

from copy import deepcopy
from datetime import date

import pytest

from tradingagents.web.capital_structure import adjust, compile_capital, validate_capital


@pytest.fixture
def capital():
    text = (
        "Issued 400 million depositary shares, each representing 1/40 interest. "
        "Preferred liquidation preference $1,000, dividend rate 6%. Split evenly between "
        "Series A conversion 2 to 3 and Series B conversion 2.5 to 4. "
        "Quarterly payments February 15, May 15, August 15, November 15, "
        "commencing August 15, 2026, ending May 15, 2029."
    )
    ref = {"source_id": "S1", "quote": text}

    def term(amount, scale=1):
        return {**ref, "amount": amount, "scale": scale}

    spec = {
        "method": "mandatory_conversion_max",
        "principal_in_other_claims": False,
        "principal_in_debt": False,
        "cash_dividends_in_fcff": False,
        "assumptions": "Static maximum conversion; cash dividends, no early conversion or fundamental change, no capped-call credit.",
        "groups": [
            {
                "name": "Issue",
                "depositary_shares": term(400, 1000000),
                "receipts_per_preferred": term(40),
                "liquidation_per_preferred": term(1000),
                "coupon_percent": term(6),
                "equal_allocation": ref,
                "conversion_date": {**ref, "date": "2029-05-15"},
                "first_dividend_date": {**ref, "date": "2026-08-15"},
                "payment_schedule": {**ref, "months": [2, 5, 8, 11], "day": 15},
                "dividend_discount_rate": 0.06,
                "discount_reason": "Independent credit risk assumption",
                "series": [
                    {"name": "A", "conversion_min": term(2), "conversion_max": term(3)},
                    {"name": "B", "conversion_min": term(2.5), "conversion_max": term(4)},
                ],
            }
        ],
    }
    docs = {"S1": {"id": "S1", "text": text, "filed": "2026-10-20"}}
    return spec, docs


def test_receipts_series_dividends_and_common_allocation(capital):
    spec, docs = capital
    b = compile_capital(spec, docs, "2026-11-27", "2026-09-30")
    assert b["conversion_shares"] == 35e6
    assert sum(s["preferred_shares"] for s in b["series"]) == 10e6
    assert b["elapsed_dividends"] == 150e6
    dates = [
        date(y, m, 15)
        for y in (2027, 2028, 2029)
        for m in (2, 5, 8, 11)
        if date(y, m, 15) <= date(2029, 5, 15)
    ]
    expected = sum(150e6 / 1.06 ** ((d - date(2026, 11, 27)).days / 365.25) for d in dates)
    assert b["future_dividend_pv"] == pytest.approx(expected)
    p = {"capital_structure": b, "shares": 100e6, "dilution": 1.0, "market_price": 90}
    out = adjust(p, {"equity_value": 20e9, "enterprise_value": 18e9})
    assert out["equity_pool"] == pytest.approx(20e9 - 150e6 - expected)
    assert out["price"] == pytest.approx((20e9 - 150e6 - expected) / 135e6)
    assert out["equity_value"] == pytest.approx(out["price"] * 100e6)
    assert out["enterprise_value"] == 18e9


@pytest.mark.parametrize(
    "fault", ["number", "date", "principal", "coupon", "allocation", "expired", "schedule", "filed_future"]
)
def test_unverified_or_double_counted_terms_blocked(capital, fault):
    spec, docs = capital
    group = spec["groups"][0]
    if fault == "filed_future":
        docs["S1"]["filed"] = "2026-11-27"
    if fault == "number":
        group["depositary_shares"]["amount"] = 401
    if fault == "date":
        group["conversion_date"]["date"] = "2029-06-15"
    if fault == "principal":
        spec["principal_in_debt"] = True
    if fault == "coupon":
        spec["cash_dividends_in_fcff"] = True
    if fault == "allocation":
        group["equal_allocation"] = {
            "source_id": "S1",
            "quote": "Preferred liquidation preference $1,000, dividend rate 6%.",
        }
    if fault == "schedule":
        group["payment_schedule"]["months"] = [2, 5, 8, 10]
    day = "2030-01-01" if fault == "expired" else "2026-11-27"
    with pytest.raises(ValueError):
        compile_capital(spec, docs, day, "2026-09-30")


def test_numeric_roundtrip_normalization(capital):
    spec, docs = capital
    b = compile_capital(spec, docs, "2026-11-27", "2026-09-30")
    p = {"capital_structure": b, "valuation_date": "2026-11-27", "balance_date": "2026-09-30"}
    expected = validate_capital(deepcopy(p))
    p["capital_structure"]["conversion_shares"] = int(b["conversion_shares"])
    assert validate_capital(p) == expected


def test_shared_engine_replay_and_no_manual_company_plan(capital, monkeypatch):
    from tradingagents.web import automatic_valuation as av, company_research, valuation as v

    spec, docs = capital
    bridge = compile_capital(spec, docs, "2026-11-27", "2026-09-30")
    job = {
        "id": "automatic-study",
        "ticker": "GOOGL",
        "date": "2026-11-27",
        "reports": {"operating_report": "Generated operating research"},
        "research_snapshot": {"company": {"profile": {}}},
    }
    p = {
        "method": "operating_fcff",
        "schema_version": 1,
        "revenue": 100e9,
        "cash": 30e9,
        "debt": 20e9,
        "shares": 1e9,
        "non_operating_assets": 2e9,
        "other_claims": 1e9,
        "operating_cash": 3e9,
        "dilution": 1.0,
        "market_price": 150.0,
        "valuation_date": job["date"],
        "balance_date": "2026-09-30",
        "forecast_start": 2027,
        "years": 10,
        "rationale": "Independent company forecast with sourced capital terms",
        "automatic_research": {"version": av.VERSION, "binding": av.binding(job)},
        "capital_structure": bridge,
        "scenarios": {},
    }
    for name in v.SCENARIOS:
        p["scenarios"][name] = {
            "growth": [0.05] * 10,
            "margins": [0.2] * 10,
            "da_ratio": 0.03,
            "capex_ratio": 0.04,
            "nwc_ratio": 0.1,
            "tax_rate": 0.2,
            "wacc": 0.1,
            "terminal_growth": 0.02,
            "terminal_roic": 0.2,
        }
    prepared = {
        "inputs": v.validate(p),
        "currency": "USD",
        "evidence": {},
        "warnings": [],
        "audit": {},
    }
    job["automatic_valuation"] = {
        "status": "completed",
        "binding": av.binding(job),
        "prepared": prepared,
    }

    def forbidden(*args):
        raise AssertionError("Manual company configuration must never be read")

    monkeypatch.setattr(company_research, "build", forbidden)
    r = v.calculate(job, p)
    assert r["sensitivity"]["prices"][2][2] == pytest.approx(r["scenarios"]["base"]["price"])
    assert r["reverse"]["status"] == "solved"
    assert r["reverse"]["reproduced_price"] == pytest.approx(p["market_price"])
    changed = deepcopy(p)
    changed["capital_structure"]["conversion_shares"] = 0
    with pytest.raises(ValueError, match="不可改写"):
        v.calculate(job, changed)
    changed = deepcopy(p)
    changed["scenarios"]["base"]["wacc"] = 0.12
    r2 = v.calculate(job, changed)
    assert (
        r2["scenarios"]["base"]["capital_adjustment"]
        == r["scenarios"]["base"]["capital_adjustment"]
    )


def test_aggregate_issuance_cannot_be_repeated_as_multiple_groups(capital):
    spec, docs = capital
    second = deepcopy(spec["groups"][0])
    second["name"] = "Duplicate aggregate issuance"
    spec["groups"].append(second)
    with pytest.raises(ValueError, match="不能重复"):
        compile_capital(spec, docs, "2026-11-27", "2026-09-30")

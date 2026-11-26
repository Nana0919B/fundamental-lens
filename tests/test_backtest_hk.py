from copy import deepcopy

import pytest

from tradingagents.web.backtest_hk import catalog, convert
from tradingagents.web.historical_valuation import calculate_history
from tradingagents.web.valuation import calculate
from tradingagents.web.valuation_backtest import historical_value, validate


def snapshot(ticker="0700.HK"):
    s = deepcopy(catalog()[ticker]["snapshots"][0])
    s.update(
        currency="HKD",
        minimum_history=1,
        fx_rows=[{"date": "2025-05-29", "close": 1.08}, {"date": "2025-05-30", "close": 9.0}],
    )
    return s


def test_prior_fx_only_and_no_share_conversion():
    source = snapshot()
    converted = convert([source], "2025-05-30")[0]
    assert converted["shares"] == source["shares"]
    assert converted["revenue"] == source["revenue"] * 1.08
    assert (
        converted["evidence"]["other_claims"]["val"]
        == source["evidence"]["other_claims"]["val"] * 1.08
    )
    assert converted["evidence"]["cash"]["fx_date"] == "2025-05-29"
    assert source["evidence"]["cash"].get("fx_date") is None
    with pytest.raises(ValueError, match="当时已知"):
        convert([source], "2025-05-29")
    with pytest.raises(ValueError, match="过旧"):
        convert([source], "2025-06-20")


@pytest.mark.parametrize("ticker", [t for t, v in catalog().items() if not v.get("exclusion")])
def test_every_hk_snapshot_uses_research_engine(ticker):
    s = convert([snapshot(ticker)], "2025-05-30")
    job, result = calculate_history(s, "2025-05-30", s[0]["shares"], 100, ticker)
    assert result["inputs"]["other_claims"] == s[0]["evidence"]["other_claims"]["val"]
    ordinary = deepcopy(job)
    ordinary.pop("historical_context")
    other = calculate(ordinary, result["inputs"])
    for key in ("scenarios", "sensitivity", "reverse", "input_digest"):
        assert result[key] == other[key]
    assert job["research_snapshot"]["company"]["profile"]["currency"] == "HKD"


def test_filing_day_and_future_fx_rejected():
    s = snapshot()
    assert "error" in historical_value([s], [], s["filed"], "0700.HK")
    job, r = calculate_history(
        convert([s], "2025-05-30"), "2025-05-30", s["shares"], 100, "0700.HK"
    )
    job["historical_context"]["source_ledger"][0]["facts"]["cash"]["fx_date"] = "2025-05-30"
    with pytest.raises(ValueError, match="汇率"):
        calculate(job, r["inputs"])


def test_currency_baskets_cannot_mix():
    with pytest.raises(ValueError, match="分别运行"):
        validate({"tickers": ["AAPL", "0700.HK"]})
    p = validate(
        {
            "tickers": list(catalog()),
            "start": "2025-05-01",
            "end": "2025-12-31",
            "inspect_date": "2025-05-30",
        }
    )
    assert len(p["tickers"]) == 13


def test_hsi_industry_coverage():
    assert len({v["industry"] for v in catalog().values()}) == 12
    assert catalog()["0669.HK"]["industry"] == "非必需性消费"
    assert catalog()["1308.HK"]["industry"] == "工业"

from copy import deepcopy

import pytest

from tradingagents.web.backtest_sec import extension_capex
from tradingagents.web.valuation_backtest import (
    annual_snapshots,
    historical_value,
    metrics,
    simulate,
    split_factor,
    validate,
)


def snapshots():
    return [
        {
            "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-01",
            "accn": str(year),
            "revenue": 1000.0,
            "operating_cf": 100.0,
            "nwc": 10.0,
            "cash_bridge": 20.0,
            "debt": 5.0,
            "shares": 10.0,
            "warnings": [],
            "evidence": {
                k: {
                    "val": v,
                    "end": f"{year}-12-31",
                    "filed": f"{year + 1}-02-01",
                    "accn": str(year),
                    "tag": k,
                    "unit": "USD",
                }
                for k, v in {
                    "revenue": 1000.0,
                    "ebit": 200.0,
                    "da": 25.0,
                    "capex": 35.0,
                    "cash": 20.0,
                }.items()
            },
        }
        for year in range(2015, 2020)
    ]


def price_rows():
    return [{"date": "2020-01-31", "close": 10.0, "split": 0}]


def test_filing_lag_future_and_split_units():
    s = snapshots()
    on = historical_value(s, price_rows(), "2020-02-01")
    after = historical_value(s, price_rows(), "2020-02-02")
    assert on["period_end"] == "2018-12-31"
    assert after["period_end"] == "2019-12-31"
    changed = deepcopy(s)
    changed[-1]["revenue"] = 1e15
    assert (
        historical_value(changed, price_rows(), "2020-02-01")["input_digest"] == on["input_digest"]
    )
    split = [{"date": "2020-01-31", "close": 2.5, "split": 0}, {"date": "2021-01-01", "split": 4}]
    split_value = historical_value(s, split, "2020-02-02")
    for name in ("bear", "base", "bull"):
        assert split_value["scenarios"][name]["price"] == pytest.approx(
            after["scenarios"][name]["price"] / 4
        )
        assert split_value["scenarios"][name]["native_price"] == pytest.approx(
            after["scenarios"][name]["native_price"]
        )
    assert split_value["native_shares"] == after["native_shares"]
    assert split_factor(split, "2021-01-01") == 1


def test_original_filing_not_later_restatement():
    def fact(value, filed, accn):
        return {
            "start": "2019-01-01",
            "end": "2019-12-31",
            "filed": filed,
            "accn": accn,
            "val": value,
            "form": "10-K",
        }

    f = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {"USD": [fact(100, "2020-02-01", "a"), fact(999, "2021-02-01", "b")]}
                }
            }
        }
    }
    s = annual_snapshots(f)
    assert s[0]["evidence"]["revenue"]["val"] == 100
    assert s[0]["filed"] == "2020-02-01"


def test_next_open_costs_dividend_and_last_unfilled():
    rows = [
        {"date": f"2020-01-{d:02d}", "open": o, "close": c, "dividend": div, "split": 0}
        for d, o, c, div in [(1, 10, 10, 1), (2, 20, 25, 2), (3, 30, 31, 3), (4, 40, 40, 4)]
    ]

    def val(bear, bull):
        return {"scenarios": {"bear": {"price": bear}, "bull": {"price": bull}}}

    values = {
        rows[0]["date"]: val(20, 100),
        rows[1]["date"]: val(1, 20),
        rows[3]["date"]: val(100, 200),
    }
    p = {"capital": 1000, "cost_bps": 100, "safety_margin": 0}
    r = simulate(rows, values, p)
    qty = 1000 / (20 * 1.01)
    assert [t["date"] for t in r["trades"]] == ["2020-01-02", "2020-01-03"]
    assert r["trades"][0]["quantity"] == pytest.approx(qty)
    # Buyer on ex-date gets no dividend; seller on next ex-date gets it.
    assert r["curve"][2]["strategy"] == pytest.approx(qty * (30 * 0.99 + 3))
    assert r["curve"][0]["benchmark"] == pytest.approx(1000 / 1.01)
    assert r["curve"][-1]["benchmark"] == pytest.approx(1000 / (10 * 1.01) * (40 + 2 + 3 + 4))
    assert "execution_date" not in r["decisions"][-1]
    assert "未成交" in r["decisions"][-1]["reason"]


def test_future_price_does_not_change_signal():
    rows = [
        {"date": "2020-01-31", "open": 10, "close": 10, "dividend": 0},
        {"date": "2020-02-03", "open": 20, "close": 21, "dividend": 0},
    ]
    values = {"2020-01-31": {"scenarios": {"bear": {"price": 15}, "bull": {"price": 30}}}}
    p = {"capital": 1000, "cost_bps": 10, "safety_margin": 0}
    a = simulate(rows, values, p)
    rows[1]["open"] = 1000
    rows[1]["close"] = 1
    b = simulate(rows, values, p)
    assert a["decisions"] == b["decisions"]
    assert a["trades"][0]["quantity"] != b["trades"][0]["quantity"]


def test_drawdown_and_invalid_input():
    r = metrics(
        [{"strategy": 120}, {"strategy": 90}, {"strategy": 110}],
        "strategy",
        100,
        "2020-01-01",
        "2021-01-01",
    )
    assert r["max_drawdown"] == -0.25
    for p in [
        {"tickers": ["XYZ"]},
        {"capital": float("nan")},
        {"cost_bps": True},
        {"start": "2025-01-01", "end": "2024-01-01"},
        {"inspect_date": "2000-01-01"},
    ]:
        with pytest.raises(ValueError):
            validate(p)


def test_stale_missing_and_nonpositive():
    s = snapshots()
    assert "error" in historical_value(s, [], "2022-01-01")
    s[-1]["error"] = "missing"
    assert "missing" in historical_value(s, price_rows(), "2020-02-02")["error"]
    s = snapshots()
    s[-1]["evidence"]["ebit"]["filed"] = "2020-02-02"
    assert "证据" in historical_value(s, price_rows(), "2020-02-02")["error"]


def test_extension_requires_whole_entity_period_and_usd():
    xml = b"""<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:n="http://nvidia.com" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"><context id="a"><entity><identifier scheme="s">1</identifier></entity><period><startDate>2019-01-01</startDate><endDate>2019-12-31</endDate></period></context><unit id="usd"><measure>iso4217:USD</measure></unit><n:PurchasesOfPropertyAndEquipmentAndIntangibleAssets contextRef="a" unitRef="usd">489000000</n:PurchasesOfPropertyAndEquipmentAndIntangibleAssets></xbrl>"""
    o = {"start": "2019-01-01", "end": "2019-12-31", "filed": "2020-02-01", "accn": "a"}
    assert extension_capex(xml, o, "url")["val"] == 489000000
    with pytest.raises(ValueError):
        extension_capex(xml, {**o, "end": "2018-12-31"}, "url")


def test_persistent_interrupted_record(tmp_path):
    from tradingagents.web import backtest_api
    from tradingagents.web.valuation_backtest import save_json

    identifier = "a" * 32
    save_json(
        tmp_path / "backtests" / f"{identifier}.json",
        {"id": identifier, "status": "running", "created_at": "2020"},
    )
    assert backtest_api.read(tmp_path, identifier)["status"] == "interrupted"
    with pytest.raises(ValueError):
        backtest_api.read(tmp_path, "../outside")


def test_same_research_entrypoint_and_full_results():
    from tradingagents.web.valuation import calculate, prepare

    value = historical_value(snapshots(), price_rows(), "2020-02-02")
    job = value["research_job"]
    saved = job["valuation"]
    ordinary = deepcopy(job)
    ordinary.pop("historical_context")
    actual = calculate(ordinary, saved["inputs"])
    for key in ("scenarios", "sensitivity", "reverse", "input_digest"):
        assert actual[key] == saved[key]
    assert saved["inputs"] == prepare(job)["inputs"]
    assert saved["inputs"]["forecast_start"] == 2021
    assert saved["inputs"]["scenarios"]["base"]["margins"][-1] == pytest.approx(0.08)
    for name in ("bear", "base", "bull"):
        assert value["scenarios"][name]["native_price"] == saved["scenarios"][name]["price"]
    changed = deepcopy(saved["inputs"])
    changed["scenarios"]["base"]["growth"][0] += 0.01
    edited = calculate(job, changed)
    assert edited["historical_context"]["assumption_status"] == "edited_retrospective_assumptions"
    changed["cash"] += 1
    with pytest.raises(ValueError, match="历史事实输入不可改写"):
        calculate(job, changed)


def test_strategy_is_invariant_to_split_units():
    rows = [
        {"date": "2020-01-31", "open": 10.0, "close": 10.0, "dividend": 0.0},
        {"date": "2020-02-03", "open": 11.0, "close": 12.0, "dividend": 0.0},
        {"date": "2020-02-04", "open": 13.0, "close": 14.0, "dividend": 1.0},
    ]
    values = {"2020-01-31": {"scenarios": {"bear": {"price": 20.0}, "bull": {"price": 30.0}}}}
    p = {"capital": 1000, "cost_bps": 10, "safety_margin": 0.2}
    a = simulate(rows, values, p)
    scaled = [{**r, **{k: r[k] / 4 for k in ("open", "close", "dividend")}} for r in rows]
    scaled_values = {"2020-01-31": {"scenarios": {"bear": {"price": 5.0}, "bull": {"price": 7.5}}}}
    b = simulate(scaled, scaled_values, p)
    assert a["curve"] == b["curve"]
    assert a["fees"] == b["fees"]


def test_research_materialization_keeps_backtest_immutable(tmp_path):
    from tradingagents.web.backtest_api import research_record
    from tradingagents.web.valuation_backtest import save_json

    identifier = "b" * 32
    value = historical_value(snapshots(), price_rows(), "2020-02-02", "AAPL")
    row = {
        "id": identifier,
        "status": "completed",
        "parameters": validate({}),
        "result": {"results": [{"ticker": "AAPL", "inspection": value, "decisions": []}]},
    }
    path = tmp_path / "backtests" / (identifier + ".json")
    save_json(path, row)
    before = path.read_bytes()
    job = research_record(tmp_path, identifier, "AAPL", "2020-02-02")
    assert job["valuation"]["input_digest"] == value["input_digest"]
    job["valuation"]["inputs"]["scenarios"]["base"]["growth"][0] = 0.9
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        research_record(tmp_path, identifier, "AAPL", "2021-01-01")


def test_research_template_cutoff_and_roundtrip():
    from tradingagents.web.valuation import calculate, validate_forecast_template

    original = historical_value(snapshots(), price_rows(), "2020-02-02", "AAPL")
    job = original["research_job"]
    inputs = deepcopy(job["valuation"]["inputs"])
    inputs["scenarios"]["base"]["growth"][0] = 0.123
    edited = calculate(job, inputs)
    template = {
        "source_job_id": "a" * 32,
        "input_digest": edited["input_digest"],
        "ticker": "AAPL",
        "as_of_date": "2020-02-02",
        "years": inputs["years"],
        "scenarios": inputs["scenarios"],
        "rationale": inputs["rationale"],
    }
    validate_forecast_template(template)
    replay = historical_value(snapshots(), price_rows(), "2020-02-02", "AAPL", template)
    assert replay["research_job"]["valuation"]["scenarios"] == edited["scenarios"]
    future = {**template, "as_of_date": "2020-03-01"}
    assert (
        "晚于" in historical_value(snapshots(), price_rows(), "2020-02-02", "AAPL", future)["error"]
    )
    with pytest.raises(ValueError, match="不能早于"):
        validate({"research_template": template, "tickers": ["AAPL"]})
    with pytest.raises(ValueError):
        validate_forecast_template({**template, "cash": 999})


def test_public_api_rejects_untrusted_or_current_research_template(tmp_path):
    from tradingagents.web.backtest_api import create
    from tradingagents.web.valuation_backtest import save_json

    with pytest.raises(ValueError, match="已保存"):
        create(tmp_path, {"research_template": {"anything": "untrusted"}})
    identifier = "c" * 32
    save_json(tmp_path / (identifier + ".json"), {"valuation": {"inputs": {"years": 5}}})
    with pytest.raises(ValueError, match="不能将当前研究"):
        create(tmp_path, {"research_job_id": identifier})

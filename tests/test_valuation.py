"""Independent closed-form and inverse checks for the FCFF valuation contract."""

import json
from copy import deepcopy

import pytest

from tradingagents.web.valuation import calculate, prepare, project, validate


def fixture_job():
    return {
        "id": "test",
        "snapshot_digest": "immutable",
        "research_snapshot": {
            "company": {
                "profile": {
                    "totalCash": 20,
                    "totalDebt": 30,
                    "sharesOutstanding": 10,
                    "currentPrice": 12.5,
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
        },
    }


def inputs():
    p = prepare(fixture_job())["inputs"]
    p.update(other_claims=5, rationale="测试：固定收入和利润率，全部现金可分配，其他权益要求为5。")
    for s in p["scenarios"].values():
        s.update(growth=[0] * 5, margins=[0.2] * 5, terminal_growth=0, wacc=0.1)
    return p


def test_closed_form_perpetuity_and_equity_bridge():
    result = calculate(fixture_job(), inputs())
    # FCFF = 100*.2*.75 + 2.5 - 3.5 = 14; perpetuity = 14/.1 = 140.
    for s in result["scenarios"].values():
        assert s["enterprise_value"] == pytest.approx(140)
        assert s["equity_value"] == pytest.approx(125)
        assert s["price"] == pytest.approx(12.5)
    assert result["sensitivity"]["prices"][2][2] == pytest.approx(12.5)
    assert result["reverse"]["margin_shift"] == pytest.approx(0, abs=1e-10)
    assert result["reverse"]["reproduced_price"] == pytest.approx(12.5)


def test_sensitivity_monotonic_and_dilution():
    p = inputs()
    r = calculate(fixture_job(), p)
    assert all(
        r["sensitivity"]["prices"][i][2] > r["sensitivity"]["prices"][i + 1][2] for i in range(4)
    )
    p["dilution"] = 2
    assert calculate(fixture_job(), p)["scenarios"]["base"]["price"] == pytest.approx(6.25)


def test_loss_year_no_tax_credit_and_terminal_reinvestment():
    p = inputs()
    s = p["scenarios"]["base"]
    s["margins"][0] = -0.1
    s["terminal_growth"] = 0.03
    r = project(p, s)
    assert r["rows"][0]["tax"] == 0
    assert r["terminal_fcff"] == pytest.approx(103 * 0.2 * 0.75 + 103 * (0.025 - 0.035) - 3 * 0.1)


@pytest.mark.parametrize(
    "key,value",
    [
        ("other_claims", None),
        ("shares", 0),
        ("revenue", float("nan")),
        ("cash", True),
        ("dilution", 0.8),
        ("years", 11),
    ],
)
def test_invalid_input_is_rejected(key, value):
    p = inputs()
    p[key] = value
    with pytest.raises(ValueError):
        validate(p)


def test_invalid_terminal_and_missing_currency():
    p = inputs()
    p["scenarios"]["base"]["terminal_growth"] = 0.1
    with pytest.raises(ValueError):
        calculate(fixture_job(), p)
    p = inputs()
    p["scenarios"]["base"]["margins"] = [-0.1] * 5
    with pytest.raises(ValueError, match="稳定期"):
        calculate(fixture_job(), p)
    job = fixture_job()
    job["research_snapshot"]["company"]["profile"]["currency"] = "HKD"
    with pytest.raises(ValueError, match="币种"):
        calculate(job, inputs())


def test_negative_equity_and_reverse_no_solution_are_explicit():
    p = inputs()
    p["debt"] = 1000
    p["market_price"] = 1e8
    r = calculate(fixture_job(), p)
    assert r["scenarios"]["base"]["price"] < 0
    assert r["scenarios"]["base"]["warning"]
    assert r["scenarios"]["base"]["upside"] is None
    assert r["reverse"]["status"] == "no_solution"


def test_snapshot_not_mutated_and_provenance_changes_with_input():
    job = fixture_job()
    before = deepcopy(job)
    a = calculate(job, inputs())
    p = inputs()
    p["other_claims"] = 6
    b = calculate(job, p)
    assert job == before
    assert a["snapshot_digest"] == "immutable"
    assert a["input_digest"] != b["input_digest"]


def test_http_persistence_and_invalid_payload(monkeypatch, tmp_path):
    from io import BytesIO
    from types import SimpleNamespace

    from tradingagents.web import server

    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "JOBS", {"test": fixture_job()})
    handler = object.__new__(server.Handler)
    handler.path = "/api/jobs/test/valuation"
    handler.server = SimpleNamespace(server_port=8765)
    responses = []
    handler.send = lambda payload, status=200: responses.append((status, payload))

    def post(payload):
        body = json.dumps(payload).encode()
        handler.headers = {
            "Host": "127.0.0.1:8765",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        handler.rfile = BytesIO(body)
        handler.do_POST()
        return responses[-1]

    status, result = post(inputs())
    assert status == 200
    assert result["scenarios"]["base"]["price"] == pytest.approx(12.5)
    assert json.loads((tmp_path / "test.json").read_text())["valuation"]["id"] == result["id"]
    assert len(list((tmp_path / "valuations" / "test").glob("*.json"))) == 1
    assert post([])[0] == 400

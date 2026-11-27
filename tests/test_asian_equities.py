"""Chinese equity input, market consistency and job routing contracts."""

import io
import json
from types import SimpleNamespace
import pytest

from tradingagents.dataflows.symbols import normalize_symbol, resolve_equity_symbol, equity_market


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("700", "0700.HK"),
        ("00700", "0700.HK"),
        ("HK00700", "0700.HK"),
        ("hk.9988", "9988.HK"),
        ("600519", "600519.SS"),
        ("SH600519", "600519.SS"),
        ("sh.688981", "688981.SS"),
        ("000001", "000001.SZ"),
        ("300750", "300750.SZ"),
        ("SZ000001", "000001.SZ"),
        ("600519.SH", "600519.SS"),
        ("AAPL", "AAPL"),
        ("0700.HK", "0700.HK"),
    ],
)
def test_canonical_codes_work_for_web_and_existing_data_layer(raw, expected):
    assert normalize_symbol(raw) == expected
    assert resolve_equity_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw,market,expected",
    [
        ("00700", "hk", "0700.HK"),
        ("600519", "cn", "600519.SS"),
        ("000001", "cn", "000001.SZ"),
        ("AAPL", "us", "AAPL"),
        ("000001", "sh", "000001.SS"),
    ],
)
def test_explicit_market_resolves_ambiguity(raw, market, expected):
    assert resolve_equity_symbol(raw, market) == expected


@pytest.mark.parametrize(
    "raw,market",
    [
        ("600519", "hk"),
        ("0700.HK", "cn"),
        ("AAPL", "hk"),
        ("000001.SZ", "sh"),
        ("600519", "us"),
        ("900001", "auto"),
        ("../secret", "auto"),
        ("00700", "unknown"),
    ],
)
def test_conflicting_or_ambiguous_markets_are_not_silently_submitted(raw, market):
    with pytest.raises(ValueError):
        resolve_equity_symbol(raw, market)


def test_hk_and_mainland_filings_do_not_contact_the_sec(monkeypatch):
    from tradingagents.dataflows.vendors import sec_edgar

    monkeypatch.setattr(sec_edgar, "cik_for", lambda *a: pytest.fail("Non-US equity sent to SEC"))
    for ticker in ("0700.HK", "600519.SS", "000001.SZ"):
        result = sec_edgar.get_filing_documents(ticker, "2026-11-13")
        assert result["documents"] == []
        assert "不适用 SEC" in result["note"]
        assert equity_market(ticker)["label"]


@pytest.mark.parametrize(
    "ticker,market,expected",
    [("00700", "hk", "0700.HK"), ("600519", "cn", "600519.SS"), ("000001", "cn", "000001.SZ")],
)
def test_real_post_handler_passes_canonical_asian_codes_to_worker(
    monkeypatch, tmp_path, ticker, market, expected
):
    from tradingagents.web import server

    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server, "STORE", tmp_path)
    calls = []
    monkeypatch.setattr(
        server, "POOL", SimpleNamespace(submit=lambda *args: calls.append(args[2]))
    )
    body = json.dumps(
        {"ticker": ticker, "market": market, "peers": ["600519.SH", "600519"]}
    ).encode()
    handler = object.__new__(server.Handler)
    handler.path = "/api/jobs"
    handler.server = SimpleNamespace(server_port=8765)
    handler.headers = {
        "Host": "127.0.0.1:8765",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    responses = []
    handler.send = lambda payload, status=200: responses.append((status, payload))
    handler.do_POST()
    assert responses[0][0] == 202
    job = server.JOBS[calls[0]]
    assert job["ticker"] == expected
    assert job["peers"] == ([] if expected == "600519.SS" else ["600519.SS"])

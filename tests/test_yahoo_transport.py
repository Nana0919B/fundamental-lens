"""Yahoo DNS override stays local, shares cookies and keeps certificate checks."""

from types import SimpleNamespace

import pandas as pd
import pytest
from curl_cffi import requests

from tradingagents.dataflows.vendors.yahoo import transport


@pytest.fixture(autouse=True)
def own_transport(monkeypatch):
    for name in ("TRADINGAGENTS_YAHOO_DNS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)
    transport._session.cache_clear()
    yield
    transport._session.cache_clear()


def test_unconfigured_transport_preserves_yfinance_defaults():
    assert transport.session_options() == {}


def test_dns_session_is_shared_and_tls_is_not_disabled(monkeypatch, tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text("test CA")
    monkeypatch.setenv("TRADINGAGENTS_YAHOO_DNS", "1.1.1.1, 1.0.0.1")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert))
    calls = []

    def session(**options):
        calls.append(options)
        return object()

    monkeypatch.setattr(requests, "Session", session)
    first = transport.session_options()["session"]
    assert transport.session_options()["session"] is first
    assert calls == [{"impersonate": "chrome", "dns": ["1.1.1.1", "1.0.0.1"], "verify": str(cert)}]
    monkeypatch.setenv("TRADINGAGENTS_YAHOO_DNS", "8.8.8.8")
    assert transport.session_options()["session"] is not first


def test_dns_without_ca_keeps_default_verification(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_YAHOO_DNS", "1.1.1.1")
    captured = {}
    monkeypatch.setattr(requests, "Session", lambda **kw: captured.update(kw) or object())
    transport.session_options()
    assert captured["dns"] == ["1.1.1.1"]
    assert "verify" not in captured  # curl defaults to True


@pytest.mark.parametrize("value", ["https://resolver.example", "not-an-address", "1.1.1.999"])
def test_invalid_resolver_is_rejected_without_network(monkeypatch, value):
    monkeypatch.setenv("TRADINGAGENTS_YAHOO_DNS", value)
    with pytest.raises(ValueError, match="DNS server IP"):
        transport.session_options()


def test_company_and_peer_fetch_use_the_configured_session(monkeypatch):
    import yfinance

    from tradingagents.dataflows.web_data import company_data, peer_data

    monkeypatch.setenv("TRADINGAGENTS_YAHOO_DNS", "1.1.1.1")
    session = object()
    monkeypatch.setattr(requests, "Session", lambda **kw: session)
    frame = pd.DataFrame({pd.Timestamp("2025-12-31"): [123.0]}, index=["Revenue"])
    item = SimpleNamespace(
        info={"longName": "The Boeing Company", "financialCurrency": "USD"},
        insider_transactions=pd.DataFrame(),
    )
    for prefix in ("", "quarterly_"):
        for attr in ("income_stmt", "balance_sheet", "cashflow"):
            setattr(item, prefix + attr, frame)
    calls = []

    def ticker(symbol, **options):
        calls.append((symbol, options))
        return item

    monkeypatch.setattr(yfinance, "Ticker", ticker)
    data = company_data("BA")
    assert data["errors"] == []
    assert all(table["rows"] for group in data["statements"].values() for table in group.values())
    assert peer_data("BA")["longName"] == "The Boeing Company"
    assert calls == [("BA", {"session": session}), ("BA", {"session": session})]

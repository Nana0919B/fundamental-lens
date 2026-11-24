"""Official Futu AppKey authentication signs the EXACT encoded request."""

import base64
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from tradingagents.dataflows.vendors.futu import FutuDataError, FutuQuoteClient


def credentials(monkeypatch, tmp_path, algorithm="ed25519"):
    private = (
        ed25519.Ed25519PrivateKey.generate()
        if algorithm == "ed25519"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    path = tmp_path / "key.pem"
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("FUTU_API_KEY", "test-app-key")
    monkeypatch.setenv("FUTU_PRIVATE_KEY_PATH", str(path))
    monkeypatch.delenv("FUTU_BASE_URL", raising=False)
    return private


@pytest.mark.parametrize("algorithm", ["ed25519", "rsa"])
def test_signature_headers_and_local_daily_dates(monkeypatch, tmp_path, algorithm):
    from tradingagents.dataflows.vendors import futu

    private = credentials(monkeypatch, tmp_path, algorithm)
    monkeypatch.setattr(futu.time, "time", lambda: 1795435200)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        headers = kwargs["headers"]
        parsed = urlsplit(url)
        payload = "\n".join(("1795435200000", "GET", parsed.path, parsed.query, "")).encode()
        signature = base64.b64decode(headers["Authorization"])
        if algorithm == "ed25519":
            private.public_key().verify(signature, payload)
        else:
            private.public_key().verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
        assert headers["X-Api-Key"] == "test-app-key"
        assert headers["X-Nonce"] and not headers["Authorization"].startswith("Bearer ")
        assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 3

        class Response:
            status_code = 200

            def json(self):
                return {
                    "ret_code": 0,
                    "data": {
                        "kline_list": [
                            {"date": 20261120, "close": 100},
                            {"date": 20261123, "close": 500},
                            {"date": 20261119, "close": 99},
                            {"date": 20261118, "close": float("inf")},
                        ]
                    },
                }

        return Response()

    monkeypatch.setattr(futu.requests, "get", get)
    frame = FutuQuoteClient(timeout=3).history("US.SPY", "2026-11-20")
    assert frame["Date"].tolist() == ["2026-11-19", "2026-11-20"]
    assert frame["Close"].tolist() == [99, 100]
    assert "ktype=2&autype=1&num=370&extended_time=0" in calls[0][0]
    assert "test-app-key" not in str(frame)


@pytest.mark.parametrize(
    "status,category",
    [(401, "authorization"), (439, "authorization"), (429, "quota"), (503, "transport")],
)
def test_http_errors_are_safe(monkeypatch, tmp_path, status, category):
    from types import SimpleNamespace

    from tradingagents.dataflows.vendors import futu

    credentials(monkeypatch, tmp_path)
    monkeypatch.setattr(futu.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=status))
    with pytest.raises(FutuDataError) as caught:
        FutuQuoteClient().history("US.SPY", "2026-11-20")
    assert caught.value.category == category and "test-app-key" not in str(caught.value)


def test_missing_private_key_network_error_and_api_error_fail_closed(monkeypatch, tmp_path):
    from tradingagents.dataflows.vendors import futu

    monkeypatch.delenv("FUTU_API_KEY", raising=False)
    monkeypatch.delenv("FUTU_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(FutuDataError, match="FUTU_API_KEY"):
        FutuQuoteClient()
    credentials(monkeypatch, tmp_path)

    def broken(*a, **kw):
        raise futu.requests.ConnectionError("test-app-key confidential-host")

    monkeypatch.setattr(futu.requests, "get", broken)
    with pytest.raises(FutuDataError) as caught:
        FutuQuoteClient().history("US.SPY", "2026-11-20")
    assert "confidential-host" not in str(caught.value)

    class Response:
        status_code = 200

        def json(self):
            return {"ret_code": -12, "ret_msg": "test-app-key"}

    monkeypatch.setattr(futu.requests, "get", lambda *a, **kw: Response())
    with pytest.raises(FutuDataError) as caught:
        FutuQuoteClient().history("US.SPY", "2026-11-20")
    assert "test-app-key" not in str(caught.value)

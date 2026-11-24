"""Read-only official Futunn REST client using AppKey request signatures.

Contract: https://open.futunn.com/api/overview/getting-started
Private keys and credentials never enter cached evidence, logs or exceptions.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

DEFAULT_BASE = "https://webapi.futunn.com"


class FutuDataError(RuntimeError):
    def __init__(self, message, category="data"):
        super().__init__(message)
        self.category = category


class FutuQuoteClient:
    """Only historical daily quote GETs; no account or trading operations."""

    def __init__(self, *, timeout=5):
        self.timeout = max(1, min(float(timeout), 30))
        self.base = (os.getenv("FUTU_BASE_URL") or DEFAULT_BASE).rstrip("/")
        url = urlsplit(self.base)
        if (
            url.username
            or url.password
            or url.query
            or url.fragment
            or url.path
            or not url.hostname
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})
            )
        ):
            raise FutuDataError("富途服务地址无效。", "configuration")
        self.app_key = os.getenv("FUTU_API_KEY", "").strip()
        key_path = os.getenv("FUTU_PRIVATE_KEY_PATH", "").strip()
        if not self.app_key or not key_path:
            raise FutuDataError(
                "请配置 FUTU_API_KEY 和 FUTU_PRIVATE_KEY_PATH（官方 AppKey 与本机签名私钥文件）。",
                "configuration",
            )
        try:
            self.private_key = serialization.load_pem_private_key(
                Path(key_path).expanduser().read_bytes(),
                password=None,
            )
            if not isinstance(self.private_key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
                raise ValueError("Unsupported signing key")
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            raise FutuDataError(
                "无法读取富途 Ed25519/RSA PEM 私钥文件。", "configuration"
            ) from None

    def _get(self, path, params):
        prepared = requests.Request("GET", self.base + path, params=params).prepare()
        parsed = urlsplit(prepared.url)
        stamp = str(int(time.time() * 1000))
        payload = "\n".join((stamp, "GET", parsed.path, parsed.query, "")).encode()
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            signature = self.private_key.sign(payload)
        else:
            signature = self.private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        headers = {
            "X-Api-Key": self.app_key,
            "X-Timestamp": stamp,
            "X-Nonce": secrets.token_urlsafe(24),
            "Authorization": base64.b64encode(signature).decode(),
        }
        try:
            response = requests.get(
                prepared.url, headers=headers, timeout=self.timeout, allow_redirects=False
            )
            if response.status_code in (401, 403, 439):
                raise FutuDataError(
                    "富途鉴权或行情权限不足，请检查 AppKey、公钥配置及签名时钟。", "authorization"
                )
            if response.status_code == 429:
                raise FutuDataError("富途行情请求超出频率或配额限制。", "quota")
            if response.status_code != 200:
                raise FutuDataError("富途行情服务暂不可用。", "transport")
            result = response.json()
        except (requests.RequestException, ValueError):
            raise FutuDataError("富途行情连接失败或返回数据无效。", "transport") from None
        if (
            not isinstance(result, dict)
            or result.get("ret_code") != 0
            or not isinstance(result.get("data"), dict)
        ):
            raise FutuDataError("富途未返回有效行情，请检查数据权限、请求配额和证券覆盖。")
        return result["data"]

    def history(self, symbol, cutoff):
        """Up to 370 forward-adjusted regular-session daily bars, inclusive cutoff."""
        if not re.fullmatch(r"[A-Z0-9_]+\.[A-Za-z0-9_.-]+", symbol):
            raise FutuDataError("富途证券代码格式无效。")
        params = {"end": cutoff, "ktype": 2, "autype": 1, "num": 370, "extended_time": 0}
        data = self._get(f"/api/v1.0/quote/{symbol}/history-kline", params)
        if not isinstance(data.get("kline_list"), list):
            raise FutuDataError("富途历史日线格式不兼容。")
        rows = []
        for row in data["kline_list"]:
            try:
                # Futu date is YYYYMMDD in the security's LOCAL market date,
                # not a Unix timestamp. Do not interpret it as nanoseconds.
                value = str(row["date"])
                if len(value) != 8:
                    continue
                day = datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
                close = float(row["close"])
                if day <= cutoff and pd.notna(close) and 0 < close < float("inf"):
                    rows.append({"Date": day, "Close": close})
            except (KeyError, TypeError, ValueError):
                continue
        return (
            pd.DataFrame(rows, columns=["Date", "Close"])
            .drop_duplicates("Date", keep="last")
            .sort_values("Date")
            .reset_index(drop=True)
        )

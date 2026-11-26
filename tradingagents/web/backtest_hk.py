"""Reviewed original HK filings; adapters only, never an alternate DCF model."""

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "research/backtests/hk-industry.json"


def catalog():
    return json.loads(MANIFEST.read_text())["companies"]


def market(ticker, cache):
    import yfinance as yf

    from tradingagents.dataflows.vendors.yahoo.transport import session_options
    from tradingagents.web.valuation_backtest import save_json

    path = cache / f"{ticker}-hk-prices-{date.today()}.json"
    if not path.exists():
        frame = yf.Ticker(ticker, **session_options()).history(
            start="2024-01-01",
            end=str(date.today() + timedelta(days=1)),
            auto_adjust=False,
            actions=True,
            raise_errors=True,
        )
        if frame.empty:
            raise ValueError(f"{ticker} 历史行情为空")
        save_json(
            path,
            [
                {
                    "date": str(i.date()),
                    "open": float(r.Open),
                    "close": float(r.Close),
                    "dividend": float(r.get("Dividends", 0)),
                    "split": float(r.get("Stock Splits", 0)),
                }
                for i, r in frame.iterrows()
            ],
        )
    rows = json.loads(path.read_text())
    import math

    if not rows or any(
        not all(math.isfinite(r[k]) for k in ("open", "close", "dividend", "split"))
        or min(r["open"], r["close"]) <= 0
        for r in rows
    ):
        raise ValueError(f"{ticker} 历史行情无效")
    return rows


def load(ticker, cache):
    item = catalog()[ticker]
    if item.get("exclusion"):
        raise ValueError(item["exclusion"])
    snapshots = deepcopy(item["snapshots"])
    for s in snapshots:
        s["currency"] = "HKD"
        s["minimum_history"] = 1
        s["source_name"] = "HKEX / issuer original annual report; reviewed inputs"
        s["fx_rows"] = (
            market(s["financial_currency"] + "HKD=X", cache)
            if s["financial_currency"] != "HKD"
            else []
        )
        s["warnings"] += [
            "港股行业试验：统一默认预测不是公司专项预测；少数股东权益、投资资产及经营必需现金尚未全面估值，不能作直接交易目标价。",
            "仅已披露年末总股数，未推断年内回购与增发；不处理每手股数、零股交易和投资者特定股息税。",
        ]
    rows = market(ticker, cache)
    import hashlib

    sources = [
        {"url": s["url"], "sha256": s["sha256"], "retrieved_at": s["retrieved_at"]}
        for s in snapshots
    ]
    sources += [
        {
            "url": f"https://finance.yahoo.com/quote/{symbol}/history/",
            "sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            "retrieved_at": str(date.today()),
        }
        for symbol, data in [(ticker, rows)]
        + [(s["financial_currency"] + "HKD=X", s["fx_rows"]) for s in snapshots if s["fx_rows"]]
    ]
    return snapshots, rows, sources


def convert(snapshots, day):
    """Previous FX close avoids using a close after the HK session."""
    out = deepcopy(snapshots)
    for s in out:
        if s.get("financial_currency", "HKD") == "HKD":
            continue
        available = [r for r in s.pop("fx_rows", []) if r["date"] < day]
        if not available:
            raise ValueError("缺少当时已知汇率")
        fx = available[-1]
        if (date.fromisoformat(day) - date.fromisoformat(fx["date"])).days > 7:
            raise ValueError("历史汇率过旧")
        rate = fx["close"]
        if not 0 < rate < 100:
            raise ValueError("历史汇率无效")
        for key in ("revenue", "debt", "cash_bridge"):
            if key in s:
                s[key] *= rate
        for key, fact in s["evidence"].items():
            if key != "shares":
                fact.update(
                    original_val=fact["val"],
                    original_currency=s["financial_currency"],
                    fx_date=fx["date"],
                    fx_rate=rate,
                    fx_pair=s["financial_currency"] + "HKD=X",
                )
                fact["val"] *= rate
        s["warnings"].append(
            f"{s['financial_currency']} 财务金额按 {fx['date']} 汇率 {rate:.6f} 换算港币；汇率早于决策日。"
        )
    return out

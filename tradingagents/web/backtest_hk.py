"""Reviewed original HK filings; adapters only, never an alternate DCF model."""

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "research/backtests/hk-industry.json"


def catalog():
    return json.loads(MANIFEST.read_text())["companies"]


def market(ticker, cache, through=None, provenance=None):
    import hashlib
    import math
    from datetime import UTC, datetime

    import yfinance as yf

    from tradingagents.dataflows.vendors.yahoo.transport import session_options
    from tradingagents.web.valuation_backtest import save_json

    def read_valid(rows):
        if not rows or any(
            not all(math.isfinite(r[k]) for k in ("open", "close", "dividend", "split"))
            or min(r["open"], r["close"]) <= 0
            for r in rows
        ):
            raise ValueError(f"{ticker} 历史行情含缺失报价；禁止补零")
        return rows

    def finish(path, rows):
        if provenance is not None:
            provenance.append(
                {
                    "url": f"https://finance.yahoo.com/quote/{ticker}/history/",
                    "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                    "archive": str(path),
                    "market_data_through": rows[-1]["date"],
                    "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                }
            )
        return rows

    # Prefer a complete immutable archive for historical replay. Its quotes and
    # split factors share the same retrieval vintage; never mix different vintages.
    if through:
        for path in sorted(cache.glob(f"{ticker}-hk-prices-*.json")):
            rows = read_valid(json.loads(path.read_text()))
            if rows[-1]["date"] > through:
                return finish(path, rows)
    path = cache / f"{ticker}-hk-prices-{date.today()}.json"
    if path.exists():
        return finish(path, read_valid(json.loads(path.read_text())))
    frame = yf.Ticker(ticker, **session_options()).history(
        start="2024-01-01",
        end=str(date.today()),
        auto_adjust=False,
        actions=True,
        raise_errors=True,
    )
    rows = read_valid(
        [
            {
                "date": str(i.date()),
                "open": float(r.Open),
                "close": float(r.Close),
                "dividend": float(r.get("Dividends", 0)),
                "split": float(r.get("Stock Splits", 0)),
            }
            for i, r in frame.iterrows()
        ]
    )
    save_json(path, rows)
    return finish(path, rows)


def load(ticker, cache, through=None):
    item = catalog()[ticker]
    if item.get("exclusion"):
        raise ValueError(item["exclusion"])
    snapshots = deepcopy(item["snapshots"])
    market_sources = []
    for s in snapshots:
        s["currency"] = "HKD"
        s["minimum_history"] = 1
        s["source_name"] = "HKEX / issuer original annual report; reviewed inputs"
        s["fx_rows"] = (
            market(s["financial_currency"] + "HKD=X", cache, through, market_sources)
            if s["financial_currency"] != "HKD"
            else []
        )
        s["warnings"] += [
            "港股行业试验：统一默认预测不是公司专项预测；少数股东权益、投资资产及经营必需现金尚未全面估值，不能作直接交易目标价。",
            "使用年末总股数及明确列示的已披露配售事件（同时调整现金）；未推断其他年内回购与增发；不处理每手股数、零股交易和投资者特定股息税。",
        ]
    rows = market(ticker, cache, through, market_sources)

    sources = [
        {"url": s["url"], "sha256": s["sha256"], "retrieved_at": s["retrieved_at"]}
        for s in snapshots
    ]
    sources += market_sources
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
            if key != "shares" and fact.get("unit") not in ("shares", "HKD"):
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

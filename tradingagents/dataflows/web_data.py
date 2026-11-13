"""Structured current company snapshots for the local research UI."""

import math
from datetime import datetime


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def frame_data(frame):
    if frame is None or frame.empty:
        return {"periods": [], "rows": []}
    frame = frame.iloc[:, :8]
    return {
        "periods": [str(c.date()) if hasattr(c, "date") else str(c) for c in frame.columns],
        "rows": [
            {"name": str(label), "values": [number(v) for v in row]}
            for label, row in frame.iterrows()
        ],
    }


def company_data(ticker):
    import yfinance as yf

    item = yf.Ticker(ticker)
    errors = []
    try:
        info = item.info or {}
    except Exception as exc:
        info = {}
        errors.append("公司概览暂不可用：" + type(exc).__name__)
    fields = (
        "longName",
        "sector",
        "industry",
        "country",
        "city",
        "website",
        "longBusinessSummary",
        "fullTimeEmployees",
        "currency",
        "financialCurrency",
        "marketCap",
        "currentPrice",
        "trailingPE",
        "forwardPE",
        "priceToBook",
        "trailingEps",
        "returnOnEquity",
        "returnOnAssets",
        "grossMargins",
        "operatingMargins",
        "profitMargins",
        "revenueGrowth",
        "earningsGrowth",
        "totalRevenue",
        "netIncomeToCommon",
        "freeCashflow",
        "totalCash",
        "totalDebt",
        "currentRatio",
        "debtToEquity",
        "sharesOutstanding",
        "dividendYield",
    )
    profile = {k: info.get(k) for k in fields}
    tables = {}
    for frequency, prefix in [("annual", ""), ("quarterly", "quarterly_")]:
        tables[frequency] = {}
        for name, attr in [
            ("income", "income_stmt"),
            ("balance", "balance_sheet"),
            ("cashflow", "cashflow"),
        ]:
            try:
                tables[frequency][name] = frame_data(getattr(item, prefix + attr))
            except Exception as exc:
                tables[frequency][name] = frame_data(None)
                errors.append(f"{frequency}/{name} 暂不可用：{type(exc).__name__}")
    return {
        "symbol": ticker,
        "profile": profile,
        "statements": tables,
        "errors": errors,
        "source": "Yahoo Finance / yfinance",
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "period_note": "财务表为供应商返回口径；季度现金流可能是累计值，请核对原始披露。",
    }


def peer_data(ticker):
    """Current peer ratios for the local UI; provider failures are explicit."""
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info or {}
        keys = [
            "longName",
            "currency",
            "financialCurrency",
            "trailingPE",
            "priceToBook",
            "returnOnEquity",
            "profitMargins",
            "revenueGrowth",
        ]
        return {"symbol": ticker, **{k: info.get(k) for k in keys}}
    except Exception:
        return {"symbol": ticker, "error": "数据暂不可用"}

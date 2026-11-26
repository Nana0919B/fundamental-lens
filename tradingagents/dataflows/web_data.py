"""Structured current company snapshots for the local research UI."""

import math
from datetime import datetime

from tradingagents.dataflows.symbols import equity_market, normalize_symbol
from tradingagents.dataflows.vendors.yahoo.transport import session_options


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def frame_data(frame):
    if frame is None or frame.empty:
        return {"periods": [], "rows": []}
    frame = frame.sort_index(axis=1, ascending=False)
    return {
        "periods": [str(c.date()) if hasattr(c, "date") else str(c) for c in frame.columns],
        "rows": [
            {"name": str(label), "values": [number(v) for v in row]}
            for label, row in frame.iterrows()
        ],
    }


def company_data(ticker):
    import yfinance as yf

    ticker = normalize_symbol(ticker)
    item = yf.Ticker(ticker, **session_options())
    errors = []
    try:
        info = item.info or {}
    except Exception as exc:
        info = {}
        errors.append("公司概览暂不可用：" + type(exc).__name__)
    fields = (
        "companyOfficers",
        "heldPercentInsiders",
        "heldPercentInstitutions",
        "mostRecentQuarter",
        "address1",
        "zip",
        "phone",
        "exchange",
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
    profile = {
        k: number(info[k]) if isinstance(info.get(k), (int, float)) else info.get(k) for k in fields
    }
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
                if not tables[frequency][name]["rows"]:
                    errors.append(f"{frequency}/{name} 数据源未提供报表")
            except Exception as exc:
                tables[frequency][name] = frame_data(None)
                errors.append(f"{frequency}/{name} 暂不可用：{type(exc).__name__}")
    try:
        transactions = item.insider_transactions
        if transactions is None or transactions.empty:
            insiders = {
                "columns": [],
                "rows": [],
                "note": "数据源未提供内部人交易记录；不等同于确认没有交易。",
            }
        else:
            recent = transactions.head(30)
            insiders = {
                "columns": [str(c) for c in recent.columns],
                "rows": [
                    [
                        None
                        if v is None or (isinstance(v, float) and not math.isfinite(v))
                        else v.isoformat()
                        if hasattr(v, "isoformat")
                        else v
                        if isinstance(v, (str, int, float, bool))
                        else str(v)
                        for v in row
                    ]
                    for row in recent.itertuples(index=False, name=None)
                ],
                "note": "供应商最近最多 30 条记录；不代表完整披露历史。",
            }
    except Exception as exc:
        insiders = {"columns": [], "rows": [], "note": "内部人交易暂不可用：" + type(exc).__name__}
    return {
        "symbol": ticker,
        "market": equity_market(ticker),
        "insider_transactions": insiders,
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

    ticker = normalize_symbol(ticker)
    try:
        info = yf.Ticker(ticker, **session_options()).info or {}
        keys = [
            "industry",
            "mostRecentQuarter",
            "longName",
            "currency",
            "financialCurrency",
            "trailingPE",
            "priceToBook",
            "returnOnEquity",
            "profitMargins",
            "revenueGrowth",
        ]
        return {
            "symbol": ticker,
            "source": "Yahoo Finance / yfinance",
            "retrieved_at": datetime.now().astimezone().isoformat(),
            **{
                k: number(info[k]) if isinstance(info.get(k), (int, float)) else info.get(k)
                for k in keys
            },
        }
    except Exception:
        return {"symbol": ticker, "error": "数据暂不可用"}

"""Hong Kong / US environment from official Futu historical quote data.

The fixed ETF basket is a transparent trend proxy, not market-wide breadth.
Rule thresholds are versioned heuristics, not calibrated return probabilities.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.dataflows.vendors.futu import FutuDataError, FutuQuoteClient

MARKETS = {
    "HK": {
        "name": "港股",
        "timezone": "Asia/Hong_Kong",
        "close_buffer": time(17),
        "currency": "HKD",
        "references": [
            ("HK.02800", "恒生指数 ETF"),
            ("HK.02828", "恒生国企指数 ETF"),
            ("HK.03033", "恒生科技指数 ETF"),
        ],
    },
    "US": {
        "name": "美股",
        "timezone": "America/New_York",
        "close_buffer": time(16, 30),
        "currency": "USD",
        "references": [
            ("US.SPY", "标普 500 ETF"),
            ("US.QQQ", "纳斯达克 100 ETF"),
            ("US.IWM", "罗素 2000 ETF"),
            ("US.RSP", "标普 500 等权 ETF"),
        ],
    },
}
METHODOLOGY = {
    "version": "international_daily_v1",
    "trend": "60*(close>MA20) + 40*(close>MA60)",
    "momentum": "clip(50 + 500*return_20d, 0, 100)",
    "breadth": "100 * proportion above MA20 in the fixed reference ETF basket; at least two aligned references",
    "volatility": "clip(100 - 200*realized_vol_20d, 0, 100)",
    "score": "0.4*trend + 0.3*momentum + 0.2*breadth + 0.1*volatility; null if breadth unavailable",
    "states": "strong >=70; lean_strong >=55; range >=45; lean_weak >=30; weak <30",
    "realized_vol_20d": "sample std of last 20 daily simple returns * sqrt(252), ratio; not implied volatility",
}


def market_for_ticker(ticker):
    if re.fullmatch(r"\d{6}\.(SS|SH|SZ)", ticker):
        return "CN_A"
    if re.fullmatch(r"\d{1,5}\.HK", ticker):
        return "HK"
    # Keep foreign exchange suffixes, futures and crypto outside the US bucket.
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z]|\.[AB])?", ticker):
        return "US"
    return None


def _features(frame, cutoff):
    if frame.empty or not {"Date", "Close"}.issubset(frame.columns):
        return pd.DataFrame()
    data = frame[["Date", "Close"]].copy()

    # Keep the local session date for timezone-aware test/provider frames.
    def local_date(value):
        try:
            return pd.Timestamp(value).date().isoformat()
        except (ValueError, TypeError):
            return None

    data["date"] = data["Date"].map(local_date)
    data["close"] = pd.to_numeric(data["Close"], errors="coerce")
    data = data[
        data["date"].notna()
        & data["date"].le(cutoff)
        & data["close"].gt(0)
        & data["close"].lt(float("inf"))
    ]
    data = data.drop_duplicates("date", keep="last").sort_values("date").set_index("date")
    close = data["close"]
    # Never backfill/forward-fill a missing session into a new date.
    data["ma20"] = close.rolling(20).mean()
    data["ma60"] = close.rolling(60).mean()
    data["return_1d"] = close.pct_change(fill_method=None)
    data["return_20d"] = close.pct_change(20, fill_method=None)
    data["realized_vol_20d"] = data["return_1d"].rolling(20).std(ddof=1) * math.sqrt(252)
    data["drawdown_60d"] = close / close.rolling(60).max() - 1
    return data.dropna(subset=["ma60", "return_20d", "realized_vol_20d"])


def build_global_environment(config, ticker, trade_date, *, market, now=None, allow_fetch=True, posthoc=False):
    from tradingagents.dataflows.market_environment import (
        _digest,
        _iso_day,
        validate_market_environment,
    )

    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    spec = MARKETS[market]
    local = now.astimezone(ZoneInfo(spec["timezone"]))
    day = _iso_day(trade_date)
    cutoff = min(
        day, local.date() - timedelta(days=local.time() < spec["close_buffer"])
    ).isoformat()
    universe = [{"symbol": symbol, "name": name} for symbol, name in spec["references"]]
    context = {
        "schema_version": 1,
        "ticker": ticker,
        "trade_date": trade_date,
        "retrieved_at": now.isoformat(),
        "cutoff_date": cutoff,
        "source": "Futu OpenAPI",
        "market": market,
        "market_name": spec["name"],
        "market_timezone": spec["timezone"],
        "currency": spec["currency"],
        "source_mode": "posthoc_latest_revision" if posthoc else "latest_revision_not_point_in_time",
        "price_basis": "forward_adjusted_daily",
        "primary_symbol": universe[0]["symbol"],
        "reference_universe": universe,
        "methodology": dict(METHODOLOGY),
        "history": [],
        "latest": None,
        "references": [],
        "mainlines": {"concept": [], "industry": []},
        "membership_note": "固定宽基/风格 ETF 样本，仅代表参考资产趋势广度，不是全市场涨跌家数或行业主线。",
        "units": {
            "return_1d": "ratio",
            "return_20d": "ratio",
            "realized_vol_20d": "ratio",
            "breadth_ratio": "ratio",
            "drawdown_60d": "ratio",
            "close": spec["currency"],
            "ma20": spec["currency"],
            "ma60": spec["currency"],
            "score": "score_0_100",
        },
        "coverage": {"status": "unavailable", "reason": "", "warnings": []},
    }
    if posthoc:
        context["refresh_note"] = "本次按原研究日期收盘口径补算市场环境；采用当前修订历史，原研究与风险结论未重新生成。"
    coverage = context["coverage"]
    if not allow_fetch:
        coverage["reason"] = "原研究未归档市场环境，恢复时不补取新数据。"
    elif not posthoc and day != now.astimezone(ZoneInfo("Asia/Shanghai")).date():
        coverage["reason"] = "历史研究必须提供当时冻结的市场环境，不使用当前修订的日线。"
    else:
        try:
            client = FutuQuoteClient(timeout=config.get("market_environment_timeout", 5))
        except FutuDataError as exc:
            coverage["reason"] = str(exc)
        else:
            frames = {}
            for reference in universe:
                symbol = reference["symbol"]
                try:
                    features = _features(client.history(symbol, cutoff), cutoff)
                    if features.empty:
                        raise FutuDataError("有效日线不足 60 根。")
                    frames[symbol] = features
                except FutuDataError as exc:
                    if symbol == context["primary_symbol"]:
                        coverage["reason"] = f"基准 {symbol}: {exc}"
                        break
                    coverage["warnings"].append(f"{symbol}: {exc}")
            primary = frames.get(context["primary_symbol"])
            if primary is not None:
                rows = []
                for date, values in primary.tail(30).iterrows():
                    aligned = [frame.loc[date] for frame in frames.values() if date in frame.index]
                    breadth = (
                        sum(v["close"] > v["ma20"] for v in aligned) / len(aligned)
                        if len(aligned) >= 2
                        else None
                    )
                    trend = 60 * int(values["close"] > values["ma20"]) + 40 * int(
                        values["close"] > values["ma60"]
                    )
                    momentum = max(0, min(100, 50 + 500 * values["return_20d"]))
                    volatility = max(0, min(100, 100 - 200 * values["realized_vol_20d"]))
                    score = (
                        round(0.4 * trend + 0.3 * momentum + 0.2 * breadth * 100 + 0.1 * volatility)
                        if breadth is not None
                        else None
                    )
                    state = next(
                        (
                            name
                            for threshold, name in [
                                (70, "strong"),
                                (55, "lean_strong"),
                                (45, "range"),
                                (30, "lean_weak"),
                                (0, "weak"),
                            ]
                            if score is not None and score >= threshold
                        ),
                        None,
                    )
                    row = {
                        key: round(float(values[key]), 6)
                        for key in (
                            "close",
                            "ma20",
                            "ma60",
                            "return_1d",
                            "return_20d",
                            "realized_vol_20d",
                            "drawdown_60d",
                        )
                    }
                    row.update(
                        date=date,
                        score=score,
                        state=state,
                        trend_score=trend,
                        momentum_score=round(momentum),
                        volatility_score=round(volatility),
                        breadth_score=round(breadth * 100) if breadth is not None else None,
                        breadth_ratio=round(breadth, 6) if breadth is not None else None,
                        breadth_sample_size=len(aligned),
                        breadth_expected_size=len(universe),
                    )
                    rows.append(row)
                context["history"] = rows
                context["latest"] = rows[-1]
                latest_date = rows[-1]["date"]
                for reference in universe:
                    frame = frames.get(reference["symbol"])
                    if frame is None or latest_date not in frame.index:
                        coverage["warnings"].append(
                            f"{reference['symbol']}: 最新观察日缺少可对齐数据"
                        )
                        continue
                    values = frame.loc[latest_date]
                    context["references"].append(
                        {
                            **reference,
                            "date": latest_date,
                            **{
                                key: round(float(values[key]), 6)
                                for key in (
                                    "close",
                                    "ma20",
                                    "ma60",
                                    "return_1d",
                                    "return_20d",
                                    "realized_vol_20d",
                                )
                            },
                        }
                    )
                coverage["status"] = "partial" if coverage["warnings"] else "available"
                if context["latest"]["score"] is None:
                    coverage["warnings"].append("可对齐的参考资产不足两只，不计算环境分。")
                    coverage["status"] = "partial"
                age = (_iso_day(cutoff) - _iso_day(latest_date)).days
                if age > int(config.get("market_environment_max_age_days", 7)):
                    coverage.update(
                        status="stale",
                        reason=f"最新环境数据距截止日期已 {age} 个自然日，仅供历史参考。",
                    )
    context["snapshot_id"] = _digest(context)
    validate_market_environment(context, ticker, trade_date)
    return context

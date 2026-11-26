"""Auditable annual-filing DCF and next-open, long/cash historical simulation.

No LLM, current fundamentals, or parameter search is used. Annual originals are
intentionally frozen instead of accepting later comparative restatements.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

VERSION = "research-fcff-backtest-2.0"
CIKS = {"AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810"}
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
RULES = {
    "valuation_entrypoint": "tradingagents.web.valuation.calculate",
    "assumptions_entrypoint": "tradingagents.web.valuation.prepare",
    "policy_version": "research-defaults-1.0",
}
LIMITATIONS = [
    "使用研究页同一 prepare/calculate 入口、逐年营收/利润率及再投资假设结构，完整保存三情景、敏感性与反向DCF。",
    "统一默认研究假设：增长2%/5%/8%，五年利润率过渡至4%/8%/12%；WACC12%/10%/9%，终值增长2%。这些假设不是历史事实或分析师共识。",
    "这是今天定义的研究假设在历史公开数据上的回放，不是当年归档的AI文字分析；没有调用今日新闻、共识或模型记忆。",
    "仅使用原始年度申报/财报，披露次日起可用；尚未使用季度更新。历史股数只按拆股调整，年报间不推断回购或新发股数。",
    "美股现金全额计入、其他权益要求0、摊薄1为显式默认假设；港股扣除所列其他权益账面值；未逐公司核验受限现金、租赁、期权、优先股等附注。",
    "沿用研究页整年折现和终值再投资算法；未建模历史估值日至完整预测年的过渡现金流。",
    "月末收盘决策，下一交易日开盘执行；悲观价值×(1−安全边际)买入，高于乐观价值卖出，全仓/现金，不加杠杆。",
    "费用包含佣金和滑点；现金无利息，除息日按前一日持仓记分红、不再投资；期末按收盘计价，不强平。",
    "买入持有采用相同费用和分红口径；未来拆股只用于行情单位归一，不用于财务预测或生成假设。",
    "选取今天的成功公司有幸存者偏差；当前SEC档案原始申报非独立封存历史库；结果不是样本外盈利保证。",
]


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须是对象")
    from tradingagents.web.backtest_hk import catalog

    hk_catalog = catalog()
    p = {
        "tickers": payload.get("tickers", list(CIKS)),
        "start": payload.get("start", "2015-01-01"),
        "end": payload.get("end", "2025-12-31"),
        "inspect_date": payload.get("inspect_date", "2020-03-23"),
    }
    if (
        not isinstance(p["tickers"], list)
        or not 1 <= len(p["tickers"]) <= 13
        or any(t not in CIKS and t not in hk_catalog for t in p["tickers"])
    ):
        raise ValueError("支持 AAPL、MSFT、NVDA 和港股行业样本，最多十三只")
    p["tickers"] = list(dict.fromkeys(p["tickers"]))
    if any(t.endswith(".HK") for t in p["tickers"]) and any(t in CIKS for t in p["tickers"]):
        raise ValueError("美元和港币样本请分别运行")
    try:
        start, end, inspect = [date.fromisoformat(p[k]) for k in ("start", "end", "inspect_date")]
    except (TypeError, ValueError):
        raise ValueError("日期格式须为 YYYY-MM-DD") from None
    if not date(2013, 1, 1) <= start < end < date.today() or not start <= inspect <= end:
        raise ValueError("开始须不早于 2013；结束早于今天；观察日在回测期间内")
    for k, default, low, high in [
        ("capital", 100000, 100, 1e9),
        ("safety_margin", 0.2, 0, 0.8),
        ("cost_bps", 10, 0, 100),
    ]:
        v = payload.get(k, default)
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or not low <= v <= high
        ):
            raise ValueError(f"{k} 超出范围 {low}～{high}")
        p[k] = v
    if payload.get("research_template"):
        from tradingagents.web.valuation import validate_forecast_template

        template = validate_forecast_template(payload["research_template"])
        if p["start"] < template["as_of_date"]:
            raise ValueError("回测开始日不能早于研究假设来源日，避免把晚期资料带回过去")
        if p["tickers"] != [template["ticker"]]:
            raise ValueError("研究假设只能用于来源股票")
        p["research_template"] = template
    return p


def save_json(path, value):
    from tradingagents.dataflows.files import replace_file

    path.parent.mkdir(parents=True, exist_ok=True)
    replace_file(
        path,
        lambda p: Path(p).write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        ),
    )


def load_sources(ticker, cache):
    import yfinance as yf

    from tradingagents.dataflows.vendors.sec_edgar import _fetch_json
    from tradingagents.dataflows.vendors.yahoo.transport import session_options

    cache.mkdir(parents=True, exist_ok=True)
    facts_file = cache / f"{ticker}-companyfacts.json"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIKS[ticker]}.json"
    if not facts_file.exists() or datetime.now().timestamp() - facts_file.stat().st_mtime > 86400:
        save_json(facts_file, _fetch_json(url))
    market_file = cache / f"{ticker}-prices-{date.today()}.json"
    if not market_file.exists():
        hist = yf.Ticker(ticker, **session_options()).history(
            start="2009-01-01",
            end=str(date.today() + timedelta(days=1)),
            auto_adjust=False,
            actions=True,
            raise_errors=True,
        )
        if hist.empty:
            raise ValueError("行情为空")
        rows = []
        for idx, row in hist.iterrows():
            values = [float(row.get(k, 0)) for k in ("Open", "Close", "Dividends", "Stock Splits")]
            if not all(math.isfinite(v) for v in values) or min(values[:2]) <= 0:
                raise ValueError(f"行情缺失或无效：{idx}")
            rows.append(
                dict(
                    zip(
                        ("date", "open", "close", "dividend", "split"),
                        [str(idx.date()), *values],
                        strict=True,
                    )
                )
            )
        save_json(market_file, rows)
    provenance = []
    for path, source in [
        (facts_file, url),
        (market_file, f"https://finance.yahoo.com/quote/{ticker}/history/"),
    ]:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        blob = cache / "blobs" / f"{digest}.json"
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(raw)
        provenance.append(
            {
                "url": source,
                "sha256": digest,
                "archive": str(blob),
                "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
        )
    return json.loads(facts_file.read_text()), json.loads(market_file.read_text()), provenance


def annual_snapshots(facts, supplemental=None):
    gaap = facts["facts"]["us-gaap"]

    def entries(tag, unit="USD"):
        return gaap.get(tag, {}).get("units", {}).get(unit, [])

    originals = {}
    for tag in REVENUE:
        for x in entries(tag):
            if (
                x.get("form") != "10-K"
                or not x.get("start")
                or not 300
                <= (date.fromisoformat(x["end"]) - date.fromisoformat(x["start"])).days
                <= 400
            ):
                continue
            # Original annual filing is shortly after year end. Older comparative
            # periods that first appear years later are not original snapshots.
            if not 0 < (date.fromisoformat(x["filed"]) - date.fromisoformat(x["end"])).days < 120:
                continue
            old = originals.get(x["end"])
            if old is None or x["filed"] < old["filed"]:
                originals[x["end"]] = x
    snapshots = []
    for end, original in sorted(originals.items()):
        evidence, warnings = {}, []

        def get(
            key,
            tags,
            annual=False,
            optional=False,
            unit="USD",
            *,
            original=original,
            end=end,
            evidence=evidence,
        ):
            for tag in tags:
                matches = [
                    x
                    for x in entries(tag, unit)
                    if x.get("accn") == original["accn"]
                    and x["end"] == end
                    and ((x.get("start") == original["start"]) if annual else not x.get("start"))
                ]
                if matches:
                    x = matches[0]
                    evidence[key] = {**x, "tag": tag, "unit": unit}
                    return float(x["val"])
            if optional:
                return None
            raise ValueError(f"缺少 {key}")

        try:
            revenue = get("revenue", REVENUE, True)
            ebit = get("ebit", ["OperatingIncomeLoss"], True)
            da = get(
                "da",
                [
                    "DepreciationDepletionAndAmortization",
                    "DepreciationAmortizationAndAccretionNet",
                    "DepreciationAndAmortization",
                ],
                True,
                True,
            )
            if da is None:
                da = get("depreciation", ["Depreciation"], True) + get(
                    "amortization",
                    [
                        "AmortizationOfIntangibleAssets",
                        "FiniteLivedIntangibleAssetsAmortizationExpense",
                    ],
                    True,
                )
            capex = get(
                "capex",
                ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
                True,
                True,
            )
            if capex is None:
                ext = (supplemental or {}).get(end)
                if ext is None or ext["accn"] != original["accn"]:
                    raise ValueError("缺少 capex")
                capex = ext["val"]
                evidence["capex"] = ext
                warnings.append("资本开支包含购买无形资产（公司扩展字段）")
            nwc = (
                get("receivables", ["AccountsReceivableNetCurrent", "AccountsReceivableNet"])
                + get("inventory", ["InventoryNet"])
                - get("payables", ["AccountsPayableCurrent"])
            )
            cash = get("cash", ["CashAndCashEquivalentsAtCarryingValue"])
            investments = get(
                "short_investments",
                [
                    "ShortTermInvestments",
                    "MarketableSecuritiesCurrent",
                    "AvailableForSaleSecuritiesCurrent",
                ],
                optional=True,
            )
            longinvest = get(
                "long_investments",
                ["MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesNoncurrent"],
                optional=True,
            )
            # Do not add generic LongTermInvestments: may include operating equity stakes.
            if investments is None or longinvest is None:
                warnings.append("未披露的标准可交易证券项目不计入现金桥；不推断其经济价值为零")
            debt = get("debt", ["LongTermDebt"], optional=True)
            if debt is None:
                noncurrent = get("debt_noncurrent", ["LongTermDebtNoncurrent"], optional=True)
                current = get("debt_current", ["LongTermDebtCurrent", "DebtCurrent"], optional=True)
                if noncurrent is None:
                    noncurrent = 0
                    warnings.append("缺少长期债务标准项，无法可靠完成股权桥")
                debt = noncurrent + (current or 0)
                if current is None:
                    warnings.append("未披露单独到期债务，按 0 假设，可能高估股权价值")
            shortdebt = get("short_debt", ["ShortTermBorrowings", "CommercialPaper"], optional=True)
            if shortdebt is None:
                warnings.append("未披露短期借款标准项，按 0 假设，可能高估股权价值")
            shares = get("shares", ["CommonStockSharesOutstanding"], unit="shares")
            if revenue <= 0 or shares <= 0:
                raise ValueError("营收或股数无效")
            snapshots.append(
                {
                    "end": end,
                    "filed": original["filed"],
                    "accn": original["accn"],
                    "revenue": revenue,
                    "operating_cf": ebit - max(ebit, 0) * 0.25 + da - capex,
                    "nwc": nwc,
                    "cash_bridge": cash + (investments or 0) + (longinvest or 0) - 0.02 * revenue,
                    "debt": debt + (shortdebt or 0),
                    "shares": shares,
                    "evidence": evidence,
                    "warnings": warnings,
                }
            )
        except ValueError as exc:
            snapshots.append(
                {"end": end, "filed": original["filed"], "error": str(exc), "evidence": evidence}
            )
    return snapshots


def split_factor(rows, after, through="9999-12-31"):
    return math.prod(r["split"] for r in rows if after < r["date"] <= through and r["split"] > 0)


def historical_value(snapshots, rows, day, ticker="UNKNOWN", template=None):
    known = [s for s in snapshots if s["filed"] < day]
    if not known or len(known) < known[-1].get("minimum_history", 4):
        return {
            "date": day,
            "error": "没有当时已公开并核验的港股原始年报"
            if ticker.endswith(".HK")
            else "不足四份当时已公开的原始年报",
        }
    recent = known[-4:]
    if ticker.endswith(".HK"):
        from tradingagents.web.backtest_hk import convert

        try:
            recent = convert(recent, day)
        except ValueError as exc:
            return {"date": day, "error": str(exc)}
    if any(s.get("error") for s in recent):
        return {
            "date": day,
            "error": "; ".join(f"{s['end']}: {s['error']}" for s in recent if s.get("error")),
        }
    latest = recent[-1]
    if "缺少长期债务标准项，无法可靠完成股权桥" in latest["warnings"]:
        return {"date": day, "error": "最新年报缺少债务字段，无法可靠完成股权桥"}
    if (date.fromisoformat(day) - date.fromisoformat(latest["end"])).days > 550:
        return {"date": day, "error": "最近年报过旧"}
    if any(
        not 300 <= (date.fromisoformat(b["end"]) - date.fromisoformat(a["end"])).days <= 400
        for a, b in zip(recent, recent[1:], strict=False)
    ):
        return {"date": day, "error": "历史年报不连续"}
    if any(split_factor(rows, s["end"], s["filed"]) != 1 for s in recent):
        return {"date": day, "error": "年末与披露间存在拆股，原始股数单位需人工核验"}
    from tradingagents.web.historical_valuation import calculate_history

    available_prices = [r for r in rows if r["date"] <= day and "close" in r]
    if not available_prices:
        return {"date": day, "error": "缺少当时行情"}
    native_factor = split_factor(rows, day)
    native_shares = latest["shares"] * split_factor(rows, latest["end"], day)
    native_price = available_prices[-1]["close"] * native_factor
    try:
        job, engine = calculate_history(recent, day, native_shares, native_price, ticker, template)
    except ValueError as exc:
        return {"date": day, "error": str(exc)}
    scenarios = {}
    for name, result in engine["scenarios"].items():
        scenario = engine["inputs"]["scenarios"][name]
        scenarios[name] = {
            **result,
            "native_price": result["price"],
            "price": result["price"] / native_factor,
            "growth": scenario["growth"][0],
            "wacc": scenario["wacc"],
            "terminal_growth": scenario["terminal_growth"],
        }
    return {
        "date": day,
        "filed": latest["filed"],
        "period_end": latest["end"],
        "accn": latest["accn"],
        "scenarios": scenarios,
        "native_shares": native_shares,
        "normalized_shares": native_shares * native_factor,
        "native_factor": native_factor,
        "cash_bridge": engine["inputs"]["cash"] - engine["inputs"]["operating_cash"],
        "debt": latest["debt"],
        "warnings": engine["warnings"],
        "source_periods": [s["end"] for s in recent],
        "research_job": job,
        "engine_version": engine["version"],
        "input_digest": engine["input_digest"],
        "snapshot_digest": engine["snapshot_digest"],
    }


def metrics(curve, key, capital, start, end):
    values = [capital] + [r[key] for r in curve]
    peak, drawdown = capital, 0
    for v in values:
        peak = max(peak, v)
        drawdown = min(drawdown, v / peak - 1)
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    return {
        "total_return": values[-1] / capital - 1,
        "cagr": (values[-1] / capital) ** (1 / years) - 1,
        "max_drawdown": drawdown,
        "ending_value": values[-1],
    }


def simulate(rows, values, p):
    capital, cost = p["capital"], p["cost_bps"] / 10000
    cash, shares, fees, benchmark_cash = capital, 0.0, 0.0, 0.0
    bh_shares = capital / (rows[0]["open"] * (1 + cost))
    pending = None
    trades, decisions, curve = [], [], []
    for i, row in enumerate(rows):
        # Dividend entitlement belongs to holders entering the ex-date.
        cash += shares * row["dividend"]
        if i:
            benchmark_cash += bh_shares * row["dividend"]
        if pending:
            if pending["action"] == "BUY":
                quantity = cash / (row["open"] * (1 + cost))
                fee = quantity * row["open"] * cost
                shares, cash = quantity, 0.0
            else:
                quantity = shares
                fee = quantity * row["open"] * cost
                cash += quantity * row["open"] - fee
                shares = 0.0
            fees += fee
            trades.append(
                {
                    "signal_date": pending["date"],
                    "date": row["date"],
                    "action": pending["action"],
                    "price": row["open"],
                    "quantity": quantity,
                    "fee": fee,
                }
            )
            pending["execution_date"] = row["date"]
            pending = None
        value = values.get(row["date"])
        if value is not None:
            decision = {
                "date": row["date"],
                "close": row["close"],
                "valuation": value,
                "action": "HOLD",
            }
            if "error" in value:
                decision["reason"] = value["error"]
            elif shares == 0 and row["close"] <= value["scenarios"]["bear"]["price"] * (
                1 - p["safety_margin"]
            ):
                decision.update(action="BUY", reason="低于悲观价值并满足安全边际")
            elif shares > 0 and row["close"] >= value["scenarios"]["bull"]["price"]:
                decision.update(action="SELL", reason="达到或超过乐观价值")
            else:
                decision["reason"] = "未触发买卖阈值，保持现有仓位"
            if decision["action"] != "HOLD":
                if i + 1 < len(rows):
                    pending = decision
                else:
                    decision["reason"] += "；回测结束，无下一交易日，未成交"
            decisions.append(decision)
        curve.append(
            {
                "date": row["date"],
                "strategy": cash + shares * row["close"],
                "benchmark": benchmark_cash + bh_shares * row["close"],
                "invested": shares > 0,
            }
        )
    return {
        "curve": curve,
        "decisions": decisions,
        "trades": trades,
        "fees": fees,
        "exposure": sum(r["invested"] for r in curve) / len(curve),
        "strategy": metrics(curve, "strategy", capital, rows[0]["date"], rows[-1]["date"]),
        "benchmark": metrics(curve, "benchmark", capital, rows[0]["date"], rows[-1]["date"]),
    }


def run_ticker(ticker, p, cache):
    is_hk = ticker.endswith(".HK")
    if is_hk:
        from tradingagents.web.backtest_hk import load

        snapshots, all_rows, provenance = load(ticker, cache)
    else:
        facts, all_rows, provenance = load_sources(ticker, cache)
    supplemental, supplement_errors = {}, {}
    if ticker == "NVDA":
        from tradingagents.web.backtest_sec import supplement

        supplemental, supplement_errors = supplement(facts, cache)
    if not is_hk:
        snapshots = annual_snapshots(facts, supplemental)
    rows = [r for r in all_rows if p["start"] <= r["date"] <= p["end"]]
    if (
        len(rows) < 2
        or (date.fromisoformat(rows[0]["date"]) - date.fromisoformat(p["start"])).days > 7
        or (date.fromisoformat(p["end"]) - date.fromisoformat(rows[-1]["date"])).days > 7
    ):
        raise ValueError("请求期间行情覆盖不足")
    if any(
        (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days > 7
        for a, b in zip(rows, rows[1:], strict=False)
    ):
        raise ValueError("行情存在超过七天的内部缺口")
    # Full month boundaries come from the source calendar, not truncated runs.
    month_ends = {
        a["date"]
        for a, b in zip(all_rows, all_rows[1:], strict=False)
        if a["date"][:7] != b["date"][:7]
    }
    values = {
        r["date"]: historical_value(
            snapshots, all_rows, r["date"], ticker, p.get("research_template")
        )
        for r in rows
        if r["date"] in month_ends
    }
    if is_hk and not any("error" not in v for v in values.values()):
        raise ValueError("期间内无有效港股估值，不能把数据不足显示为零交易策略")
    result = simulate(rows, values, p)
    for decision in result["decisions"]:
        decision["native_close"] = decision["close"] * split_factor(all_rows, decision["date"])
    inspect_rows = [r for r in rows if r["date"] <= p["inspect_date"]]
    if not inspect_rows:
        raise ValueError("观察日之前没有交易日，请选择之后的日期")
    selected = inspect_rows[-1]
    inspection = historical_value(
        snapshots, all_rows, selected["date"], ticker, p.get("research_template")
    )
    inspection["close"] = selected["close"]
    inspection["native_close"] = selected["close"] * split_factor(all_rows, selected["date"])
    for t in result["trades"]:
        t["native_price"] = t["price"] * split_factor(all_rows, t["date"])
    result.update(
        ticker=ticker,
        currency="HKD" if is_hk else "USD",
        inspection=inspection,
        snapshots=[{k: v for k, v in s.items() if k != "fx_rows"} for s in snapshots],
        sources=provenance,
        supplement_errors=supplement_errors,
        valuation_coverage={
            "valid": sum("error" not in v for v in values.values()),
            "total": len(values),
        },
    )
    return result


def run(payload, cache):
    p = validate(payload)
    result = {
        "version": VERSION,
        "parameters": p,
        "rules": RULES,
        "limitations": LIMITATIONS
        + (
            [
                "港股：仅测试已有核验原始年报覆盖的期间；财报金额用决策日前一可用日汇率换算HKD；总股数包含A/H股。",
                "港股默认预测仍与研究页相同，非行业定制；未完成股权桥、金融机构或SOTP项目单独排除，不视为零交易。",
            ]
            if any(t.endswith(".HK") for t in p["tickers"])
            else []
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "results": [],
        "errors": {},
        "excluded": {},
    }
    for ticker in p["tickers"]:
        if ticker.endswith(".HK"):
            from tradingagents.web.backtest_hk import catalog

            item = catalog()[ticker]
            if item.get("exclusion"):
                result["excluded"][ticker] = item
                continue
        try:
            row = run_ticker(ticker, p, cache)
            if ticker.endswith(".HK"):
                row.update(name=item["name"], industry=item["industry"])
            result["results"].append(row)
        except Exception as exc:
            result["errors"][ticker] = f"{type(exc).__name__}: {exc}"
    return result

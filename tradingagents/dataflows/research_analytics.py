"""Deterministic financial calculations on the existing shared snapshot.

No vendor calls: every computed result identifies its source cells and formula.
"""

from datetime import date
import math

ALIASES = {
    "revenue": ("income", ("Total Revenue", "Operating Revenue")),
    "gross_profit": ("income", ("Gross Profit",)),
    "operating_income": ("income", ("Operating Income",)),
    "net_income": ("income", ("Net Income", "Net Income Common Stockholders")),
    "assets": ("balance", ("Total Assets",)),
    "equity": ("balance", ("Stockholders Equity", "Common Stock Equity")),
    "current_assets": ("balance", ("Current Assets",)),
    "current_liabilities": ("balance", ("Current Liabilities",)),
    "liabilities": ("balance", ("Total Liabilities Net Minority Interest", "Total Liabilities")),
    "debt": ("balance", ("Total Debt",)),
    "cash": (
        "balance",
        ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    ),
    "operating_cashflow": ("cashflow", ("Operating Cash Flow",)),
    "capex": ("cashflow", ("Capital Expenditure",)),
}
FORMULAS = {
    "gross_margin": ("毛利率", "gross_profit / revenue", "ratio"),
    "operating_margin": ("经营利润率", "operating_income / revenue", "ratio"),
    "net_margin": ("净利率", "net_income / revenue", "ratio"),
    "roe": ("ROE（平均净资产）", "net_income / ((equity + prior_equity) / 2)", "ratio"),
    "roa": ("ROA（平均总资产）", "net_income / ((assets + prior_assets) / 2)", "ratio"),
    "current_ratio": ("流动比率", "current_assets / current_liabilities", "multiple"),
    "debt_ratio": ("资产负债率", "liabilities / assets", "ratio"),
    "free_cashflow": (
        "自由现金流",
        "operating_cashflow + capex (Yahoo outflow-negative)",
        "currency",
    ),
}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def evidence_catalog(company):
    result = []
    for key, value in company.get("profile", {}).items():
        if value is not None and not isinstance(value, (dict, list)):
            result.append(
                {
                    "id": f"profile.{key}",
                    "field": key,
                    "value": value,
                    "period": "current snapshot",
                    "source": company.get("source"),
                }
            )
    for freq, tables in company.get("statements", {}).items():
        for name, table in tables.items():
            for row_index, row in enumerate(table.get("rows", [])):
                for i, period in enumerate(table.get("periods", [])):
                    value = row.get("values", [])[i] if i < len(row.get("values", [])) else None
                    if value is not None:
                        result.append(
                            {
                                "id": f"{freq}.{name}.{row_index}.{i}",
                                "field": row["name"],
                                "value": value,
                                "period": period,
                                "frequency": freq,
                                "currency": company.get("profile", {}).get("financialCurrency"),
                                "source": company.get("source"),
                            }
                        )
    return result


def series(company, freq):
    output = {}
    for key, (table_name, aliases) in ALIASES.items():
        table = company.get("statements", {}).get(freq, {}).get(table_name, {})
        rows = table.get("rows", [])
        # Choose one tag, never sum aliases or mix them across periods.
        match = next(
            ((i, r) for alias in aliases for i, r in enumerate(rows) if r["name"] == alias), None
        )
        if not match:
            continue
        row_index, row = match
        for i, period in enumerate(table.get("periods", [])):
            value = row.get("values", [])[i] if i < len(row.get("values", [])) else None
            if finite(value):
                output.setdefault(period, {})[key] = (value, f"{freq}.{table_name}.{row_index}.{i}")
    return output


def safe_div(a, b):
    return a / b if finite(a) and finite(b) and b > 0 else None


def metric_history(company):
    annual = series(company, "annual")
    history = []
    periods = sorted(annual, reverse=True)
    for i, period in enumerate(periods):
        current = annual[period]
        previous = annual[periods[i + 1]] if i + 1 < len(periods) else {}
        contiguous = (
            i + 1 < len(periods)
            and 300 <= (date.fromisoformat(period) - date.fromisoformat(periods[i + 1])).days <= 400
        )

        def value(key):
            return current.get(key, (None, None))[0]

        def prior(key):
            return previous.get(key, (None, None))[0] if contiguous else None

        def ratio(a, b):
            return safe_div(value(a), value(b))

        def avg_ratio(n, d):
            a, b = value(d), prior(d)
            return (
                safe_div(value(n), (a + b) / 2)
                if a is not None and b is not None and a > 0 and b > 0
                else None
            )

        ocf, capex = value("operating_cashflow"), value("capex")
        values = {
            "gross_margin": ratio("gross_profit", "revenue"),
            "operating_margin": ratio("operating_income", "revenue"),
            "net_margin": ratio("net_income", "revenue"),
            "roe": avg_ratio("net_income", "equity"),
            "roa": avg_ratio("net_income", "assets"),
            "current_ratio": ratio("current_assets", "current_liabilities"),
            "debt_ratio": ratio("liabilities", "assets"),
            "free_cashflow": ocf + capex
            if ocf is not None and capex is not None and capex <= 0
            else None,
        }
        refs = [v[1] for v in current.values()] + (
            [v[1] for v in previous.values()] if contiguous else []
        )
        history.append(
            {"period": period, "frequency": "annual", "values": values, "evidence_refs": refs}
        )
    growth = []
    for freq, lag, low, high in [
        ("annual", 1, 300, 400),
        ("quarterly", 1, 60, 120),
        ("quarterly", 4, 330, 400),
    ]:
        data = series(company, freq)
        ps = sorted(data, reverse=True)
        for i, period in enumerate(ps):
            if i + lag >= len(ps):
                continue
            before = ps[i + lag]
            span = (date.fromisoformat(period) - date.fromisoformat(before)).days
            if not low <= span <= high:
                continue
            for field in ("revenue", "net_income"):
                a, b = data[period].get(field), data[before].get(field)
                if not a or not b:
                    continue
                growth.append(
                    {
                        "field": field,
                        "period": period,
                        "base_period": before,
                        "basis": "QoQ" if freq == "quarterly" and lag == 1 else "YoY",
                        "frequency": freq,
                        "value": (a[0] - b[0]) / b[0] if b[0] > 0 else None,
                        "note": None if b[0] > 0 else "基期非正，增长率无意义",
                        "evidence_refs": [a[1], b[1]],
                    }
                )
    quarterly = series(company, "quarterly")
    ps = sorted(quarterly, reverse=True)[:4]
    ttm = {
        "periods": ps,
        "values": {},
        "evidence_refs": [],
        "note": "仅汇总连续四个季度的利润表项目；不对可能累计的季度现金流直接求和。",
    }
    consecutive = len(ps) == 4 and all(
        60 <= (date.fromisoformat(ps[i]) - date.fromisoformat(ps[i + 1])).days <= 120
        for i in range(3)
    )
    for key in ("revenue", "net_income", "gross_profit", "operating_income"):
        cells = [quarterly[p].get(key) for p in ps]
        ttm["values"][key] = (
            sum(c[0] for c in cells) if consecutive and all(c is not None for c in cells) else None
        )
        if ttm["values"][key] is not None:
            ttm["evidence_refs"].extend(c[1] for c in cells)
    return {
        "formula_version": "1.0",
        "definitions": {
            k: {"label": v[0], "formula": v[1], "unit": v[2]} for k, v in FORMULAS.items()
        },
        "history": history,
        "growth": growth,
        "ttm": ttm,
        "limitations": [
            "历史指标按同一年度报告日匹配报表；平均余额需相邻年度。分母非正或缺失时不计算。",
            "供应商当前修订值不是历史时点可得值。金额不跨币种合并，未做汇率转换。",
            "自由现金流仅在资本开支为流出负值时计算；季度现金流累计口径不明时不计算 TTM。",
        ],
    }


def compare_peers(company, peers):
    main = {"symbol": company["symbol"], **company.get("profile", {})}
    rows = []
    for item in [main, *peers]:
        row = {
            k: item.get(k)
            for k in (
                "symbol",
                "longName",
                "industry",
                "currency",
                "financialCurrency",
                "trailingPE",
                "priceToBook",
                "returnOnEquity",
                "profitMargins",
                "revenueGrowth",
            )
        }
        row["financial_period"] = item.get("mostRecentQuarter")
        row["same_industry"] = bool(
            main.get("industry") and item.get("industry") == main.get("industry")
        )
        row["period_aligned"] = bool(
            main.get("mostRecentQuarter")
            and item.get("mostRecentQuarter") == main.get("mostRecentQuarter")
        )
        row["error"] = item.get("error")
        rows.append(row)
    # Percentiles are meaningful only for the explicitly selected, aligned sample.
    distributions = []
    aligned = [r for r in rows if r["same_industry"] and r["period_aligned"] and not r["error"]]
    for key in ("trailingPE", "priceToBook", "returnOnEquity", "profitMargins", "revenueGrowth"):
        sample = [r[key] for r in aligned if finite(r.get(key))]
        v = main.get(key)
        distributions.append(
            {
                "metric": key,
                "sample_size": len(sample),
                "percentile": 100 * sum(x < v for x in sample) / len(sample)
                if finite(v) and len(sample) >= 3
                else None,
            }
        )
    return {
        "rows": rows,
        "distributions": distributions,
        "basis": "供应商当前比率；财报日一致且行业标签相同的所选样本才计算分位。不是全行业分位。",
        "currency_policy": "只比较比率，不跨币种合并金额；PE/PB 为市场快照，其余为供应商最近期间口径。",
    }


def enrich_snapshot(snapshot):
    snapshot["analytics"] = metric_history(snapshot["company"])
    snapshot["evidence"] = evidence_catalog(snapshot["company"])
    snapshot["comparison"] = compare_peers(snapshot["company"], snapshot.get("peers", []))
    return snapshot

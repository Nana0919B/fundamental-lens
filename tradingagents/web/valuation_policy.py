"""Versioned ex-ante rules: operating history only; no prices or trading results.

Ranges are research judgments, NOT current industry averages or historical
consensus. Freeze this policy before executing comparisons; never optimize it
against the comparison sample.
"""

import hashlib
from pathlib import Path
from statistics import median, pstdev

VERSION = "history-sector-2.0"
# trend, growth floor/cap, scenario growth spread, margin floor/cap/stress,
# D&A fallback, capex fallback, working-capital intensity, cash reserve, terminal ROC.
SECTORS = {
    "hardware": (0.03, -0.10, 0.20, 0.05, 0.04, 0.35, 0.04, 0.025, 0.03, 0.10, 0.03, 0.15),
    "software": (0.05, -0.05, 0.25, 0.06, 0.08, 0.50, 0.05, 0.05, 0.07, 0.05, 0.03, 0.18),
    "semiconductors": (0.05, -0.20, 0.35, 0.10, 0.03, 0.55, 0.08, 0.04, 0.05, 0.20, 0.04, 0.15),
    "internet": (0.04, -0.10, 0.25, 0.06, 0.05, 0.40, 0.05, 0.04, 0.06, 0.05, 0.03, 0.15),
    "telecom": (0.02, -0.05, 0.08, 0.03, 0.06, 0.25, 0.03, 0.17, 0.18, 0.05, 0.03, 0.10),
    "staples": (0.02, -0.05, 0.10, 0.03, 0.05, 0.25, 0.03, 0.04, 0.05, 0.10, 0.03, 0.12),
    "oil_gas": (0.01, -0.15, 0.12, 0.08, 0.10, 0.45, 0.10, 0.18, 0.25, 0.10, 0.04, 0.10),
    "mining": (0.02, -0.15, 0.15, 0.08, 0.05, 0.25, 0.06, 0.05, 0.08, 0.15, 0.04, 0.10),
    "tools": (0.03, -0.10, 0.15, 0.05, 0.04, 0.18, 0.03, 0.03, 0.04, 0.20, 0.03, 0.12),
    "utilities": (0.02, -0.05, 0.08, 0.03, 0.06, 0.25, 0.03, 0.10, 0.15, 0.05, 0.02, 0.08),
    "auto": (0.03, -0.15, 0.25, 0.08, 0.02, 0.15, 0.03, 0.06, 0.08, 0.10, 0.04, 0.12),
    "pharma": (0.04, -0.10, 0.20, 0.06, 0.05, 0.30, 0.05, 0.04, 0.05, 0.10, 0.04, 0.15),
    "shipping": (0.01, -0.20, 0.15, 0.10, 0.05, 0.35, 0.10, 0.10, 0.15, 0.05, 0.04, 0.10),
}
TICKERS = {
    "AAPL": "hardware",
    "MSFT": "software",
    "NVDA": "semiconductors",
    "0700.HK": "internet",
    "0941.HK": "telecom",
    "0291.HK": "staples",
    "0883.HK": "oil_gas",
    "2899.HK": "mining",
    "0669.HK": "tools",
    "0002.HK": "utilities",
    "1211.HK": "auto",
    "1177.HK": "pharma",
    "1308.HK": "shipping",
}
CYCLICAL = {"semiconductors", "oil_gas", "mining", "auto", "shipping"}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def series(company, kind, name):
    table = company.get("statements", {}).get("annual", {}).get(kind, {})
    row = next((r for r in table.get("rows", []) if r["name"] == name), {})
    return {
        d: v
        for d, v in zip(table.get("periods", []), row.get("values", []), strict=False)
        if isinstance(v, (int, float))
    }


def build(company, ticker):
    sector = TICKERS.get(ticker.upper())
    if sector is None:
        return None
    trend, glo, ghi, spread, mlo, mhi, stress, da_default, cx_default, nwc, reserve, roc = SECTORS[
        sector
    ]
    revenue = series(company, "income", "Total Revenue")
    ebit = series(company, "income", "Operating Income")
    periods = sorted(revenue, reverse=True)[:4]
    if not periods or not all(revenue[d] > 0 for d in periods) or periods[0] not in ebit:
        return None
    # Only adjacent fiscal periods, never compare non-contiguous years as one year.
    from datetime import date

    growth = [
        revenue[a] / revenue[b] - 1
        for a, b in zip(periods, periods[1:], strict=False)
        if 300 <= (date.fromisoformat(a) - date.fromisoformat(b)).days <= 400
    ]
    margins = [ebit[d] / revenue[d] for d in periods if d in ebit]
    observation = median(growth) if growth else trend
    weight = 0.4 if sector in CYCLICAL else 0.7
    anchor = clamp(weight * observation + (1 - weight) * trend, glo, ghi)
    normalized = clamp(0.4 * margins[0] + 0.6 * median(margins), mlo, mhi)
    margin_stress = max(stress, min(pstdev(margins), stress * 2))
    ratios = {}
    fallbacks = []
    for key, row_name, default in [
        ("da_ratio", "Depreciation And Amortization", da_default),
        ("capex_ratio", "Capital Expenditure", cx_default),
    ]:
        data = series(company, "cashflow", row_name)
        values = [abs(data[d]) / revenue[d] for d in periods if d in data]
        ratios[key] = clamp(median(values), 0, 1) if values else default
        if not values:
            fallbacks.append(key)
    scenarios = {}
    for name, sign, wacc in [("bear", -1, 0.12), ("base", 0, 0.10), ("bull", 1, 0.09)]:
        first = clamp(anchor + sign * spread, glo, ghi)
        final = clamp(trend + sign * 0.02, glo, ghi)
        target = clamp(normalized + sign * margin_stress, mlo, mhi)
        scenarios[name] = {
            "growth": [first + (final - first) * i / 4 for i in range(5)],
            "margins": [margins[0] + (target - margins[0]) * (i + 1) / 5 for i in range(5)],
            **ratios,
            "nwc_ratio": nwc,
            "tax_rate": 0.25,
            "wacc": wacc,
            "terminal_growth": 0.02,
            "terminal_roic": roc,
            "asset_haircut": {"bear": 0.30, "base": 0.15, "bull": 0}[name],
        }
    rationale = (
        f"{VERSION} / {sector}：以可见年报营收增速中位数{observation:.2%}为观察值，"
        f"权重{weight:.0%}与行业长期增长假设{trend:.2%}混合；增长逐年衰减。"
        f"正常化利润率{normalized:.2%}由最近与历史利润率混合，情景压力±{margin_stress:.2%}。"
        f"稳定期再投资率=g/ROIC，ROIC假设{roc:.2%}；经营现金储备为收入{reserve:.1%}且不超过现金。"
        "非经营资产单独计入并施加30%/15%/0%价值折扣；不是已核验市场价值。"
        "WACC仍12%/10%/9%；买卖规则不变。参数为预先定义研究假设，不是历史共识。"
    )
    return {
        "scenarios": scenarios,
        "operating_cash_ratio": reserve,
        "rationale": rationale,
        "audit": {
            "version": VERSION,
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sector": sector,
            "periods": periods,
            "observed_growth": growth,
            "observed_margins": margins,
            "growth_anchor": anchor,
            "normalized_margin": normalized,
            "fallback_ratios": fallbacks,
            "one_year_only": not growth,
        },
        "warnings": (
            ["增长与波动仅有不足三年的可见历史，样本不足；情景范围为研究先验。"]
            if len(periods) < 3
            else []
        )
        + (
            [f"资本再投资参数 {', '.join(fallbacks)} 缺少完整原始数据，使用显式行业假设。"]
            if fallbacks
            else []
        ),
    }

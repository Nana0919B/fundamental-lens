"""Dated company-FCF bridge to unlevered cash flow and mandatory conversion.

Company FCF (CFO-capex) is not FCFF. Adjust financing income/expense and the
cash equivalent of employee equity compensation before discounting at WACC.
"""

from copy import deepcopy
from datetime import date

from tradingagents.web.valuation import numeric


def day(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 日期须为 YYYY-MM-DD") from exc


def validate_cashflow(payload):
    p = deepcopy(payload)
    value_date = day(p.get("valuation_date"), "估值日")
    balance_date = day(p.get("balance_date"), "资产负债表日")
    stub_end = day(p.get("stub_end"), "过渡期末")
    if not balance_date <= value_date < stub_end or (stub_end - balance_date).days > 366:
        raise ValueError("必须满足资产负债表日 ≤ 估值日 < 过渡期末（最长一年）")
    if stub_end.month != 12 or stub_end.day != 31:
        raise ValueError("当前过渡期须截至 12 月 31 日")
    p["forecast_start"] = stub_end.year + 1
    if (
        not isinstance(p.get("years"), int)
        or isinstance(p["years"], bool)
        or not 3 <= p["years"] <= 10
    ):
        raise ValueError("预测期须为 3 至 10 年")
    for k in ["shares", "market_price"]:
        p[k] = numeric(p.get(k), k, 0.000001, 1e15)
    for k in [
        "cash",
        "debt",
        "operating_cash",
        "other_claims",
        "paid_preferred_dividends",
        "ytd_company_fcf",
    ]:
        p[k] = numeric(p.get(k), k, -1e15 if k == "ytd_company_fcf" else 0, 1e15)
    if p["operating_cash"] > p["cash"]:
        raise ValueError("经营必需现金不能超过披露现金")
    if not isinstance(p.get("rationale"), str) or not 10 <= len(p["rationale"]) <= 6000:
        raise ValueError("请提供假设依据（10 至 6000 字）")
    preferred = p.get("preferred")
    if not isinstance(preferred, dict):
        raise ValueError("缺少强制转股条款")
    for k in ["count", "liquidation_per_share", "min_ratio", "max_ratio", "selected_ratio"]:
        preferred[k] = numeric(preferred.get(k), k, 0.000001, 1e12)
    if not preferred["min_ratio"] <= preferred["selected_ratio"] <= preferred["max_ratio"]:
        raise ValueError("转股比例必须在已核实的上下限内")
    preferred["coupon"] = numeric(preferred.get("coupon"), "优先股股息率", 0, 0.3)
    conversion = day(preferred.get("conversion_date"), "强制转股日")
    if conversion <= value_date:
        raise ValueError("转股日已过，应更新普通股股数及优先股状态")
    dates = preferred.get("dividend_dates")
    if not isinstance(dates, list) or len(dates) > 40 or not dates:
        raise ValueError("缺少转股前剩余股息日期")
    parsed = [day(d, "股息支付日") for d in dates]
    if parsed != sorted(set(parsed)) or any(not value_date < d <= conversion for d in parsed):
        raise ValueError("股息日期必须有序、无重复、晚于估值日且不晚于转股日")
    if p.get("method") != "company_fcf_bridge":
        raise ValueError("现金流模型类型无效")
    if not isinstance(p.get("scenarios"), dict) or set(p["scenarios"]) != {"bear", "base", "bull"}:
        raise ValueError("需要三个完整情景")
    for name, s in p["scenarios"].items():
        if not isinstance(s, dict):
            raise ValueError("情景参数必须为对象")
        for k, lo, hi in [
            ("company_fcf", -1e15, 1e15),
            ("interest", 0, 1e15),
            ("interest_income", 0, 1e15),
            ("equity_comp", 0, 1e15),
            ("interest_tax_rate", 0, 0.6),
        ]:
            a = s.get(k)
            if not isinstance(a, list) or len(a) != p["years"]:
                raise ValueError(f"{name}/{k} 需要 {p['years']} 个逐年参数")
            s[k] = [numeric(v, k, lo, hi) for v in a]
        for k, lo, hi in [
            ("wacc", 0.01, 0.5),
            ("terminal_growth", -0.05, 0.08),
            ("preferred_discount_rate", 0, 0.3),
            ("fy_company_fcf", -1e15, 1e15),
            ("stub_interest", 0, 1e15),
            ("stub_interest_income", 0, 1e15),
            ("stub_equity_comp", 0, 1e15),
            ("stub_tax_rate", 0, 0.6),
            ("terminal_normalization", 0, 1e15),
        ]:
            s[k] = numeric(s.get(k), k, lo, hi)
        adjustments = s.get("wacc_adjustments", [0] * p["years"])
        if not isinstance(adjustments, list) or len(adjustments) != p["years"]:
            raise ValueError("WACC 税盾调整需要逐年参数")
        s["wacc_adjustments"] = [numeric(a, "WACC 税盾调整", 0, 0.1) for a in adjustments]
        s["stub_wacc_adjustment"] = numeric(
            s.get("stub_wacc_adjustment", 0), "过渡期税盾调整", 0, 0.1
        )
        if s["wacc"] - s["terminal_growth"] < 0.005 - 1e-12:
            raise ValueError("WACC 必须至少高于永续增长率 0.5 个百分点")
    sources = p.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 30:
        raise ValueError("来源列表格式无效")
    for source in sources:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("url"), str)
            or not source["url"].startswith("https://")
        ):
            raise ValueError("来源需要 HTTPS 链接")
    return p


def cashflow_discount(p, s, payment_date):
    """Piecewise discount curve; no current debt tax shield before cash taxes resume."""
    value_date = day(p["valuation_date"], "估值日")
    stub_end = day(p["stub_end"], "过渡期末")
    current = value_date
    factor = 1.0
    intervals = [(stub_end, s.get("stub_wacc_adjustment", 0))]
    intervals += [
        (date(p["forecast_start"] + i, 12, 31), adj)
        for i, adj in enumerate(s.get("wacc_adjustments", [0] * p["years"]))
    ]
    for end, adjustment in intervals:
        finish = min(end, payment_date)
        if finish > current:
            factor *= (1 + s["wacc"] + adjustment) ** ((finish - current).days / 365)
            current = finish
        if current >= payment_date:
            return factor
    if payment_date > current:
        factor *= (1 + s["wacc"]) ** ((payment_date - current).days / 365)
    return factor


def forecast_cashflow(p, s):
    value_date, balance_date, stub_end = (
        day(p[k], k) for k in ["valuation_date", "balance_date", "stub_end"]
    )
    period_days = (stub_end - balance_date).days
    elapsed = (value_date - balance_date).days / period_days
    remaining = 1 - elapsed
    q4_fcf = s["fy_company_fcf"] - p["ytd_company_fcf"]
    # Debt issuance/repayment offsets cash and debt equally; use a net-cash bridge.
    # Company FCF already includes interest; financing adjustments only affect future FCFF.
    rolled_cash = p["cash"] + q4_fcf * elapsed - p["paid_preferred_dividends"]
    q4_fcff = (
        q4_fcf
        + (s["stub_interest"] - s["stub_interest_income"]) * (1 - s["stub_tax_rate"])
        - s["stub_equity_comp"]
    )
    stub_mid = value_date + (stub_end - value_date) / 2
    stub_pv = q4_fcff * remaining / cashflow_discount(p, s, stub_mid)
    rows = []
    for i, fcf in enumerate(s["company_fcf"]):
        fcff = (
            fcf
            + (s["interest"][i] - s["interest_income"][i]) * (1 - s["interest_tax_rate"][i])
            - s["equity_comp"][i]
        )
        year = p["forecast_start"] + i
        # Mid-year convention, actual calendar days to valuation date.
        start, end = date(year, 1, 1), date(year, 12, 31)
        middle = start + (end - start) / 2
        years = (middle - value_date).days / 365
        rows.append(
            {
                "year": year,
                "company_fcf": fcf,
                "interest": s["interest"][i],
                "interest_income": s["interest_income"][i],
                "interest_tax_rate": s["interest_tax_rate"][i],
                "equity_comp": s["equity_comp"][i],
                "fcff": fcff,
                "discount_years": years,
                "pv": fcff / cashflow_discount(p, s, middle),
            }
        )
    normalized = rows[-1]["fcff"] - s["terminal_normalization"]
    if normalized <= 0:
        raise ValueError("扣除临时现金释放后稳定期 FCFF 必须为正")
    terminal_fcff = normalized * (1 + s["terminal_growth"])
    terminal_date = date(p["forecast_start"] + p["years"] - 1, 12, 31)
    # Annual perpetuity is valued at the last year end; first payment next year end.
    terminal_pv = (
        terminal_fcff / (s["wacc"] - s["terminal_growth"]) / cashflow_discount(p, s, terminal_date)
    )
    enterprise = stub_pv + sum(r["pv"] for r in rows) + terminal_pv
    pref = p["preferred"]
    dividend = pref["count"] * pref["liquidation_per_share"] * pref["coupon"] / 4
    dividend_rows = [
        {
            "date": d,
            "amount": dividend,
            "pv": dividend
            / (1 + s["preferred_discount_rate"]) ** ((day(d, d) - value_date).days / 365),
        }
        for d in pref["dividend_dates"]
    ]
    dividend_pv = sum(r["pv"] for r in dividend_rows)
    pool = (
        enterprise + rolled_cash - p["operating_cash"] - p["debt"] - p["other_claims"] - dividend_pv
    )
    extra_shares = pref["count"] * pref["selected_ratio"]
    diluted = p["shares"] + extra_shares
    price = pool / diluted
    return {
        "rows": rows,
        "terminal_fcff": terminal_fcff,
        "terminal_pv": terminal_pv,
        "enterprise_value": enterprise,
        "equity_pool": pool,
        "equity_value": price * p["shares"],
        "converted_preferred_equity_value": price * extra_shares,
        "price": price,
        "effective_shares": diluted,
        "conversion_shares": extra_shares,
        "conversion_price_range": sorted(
            [
                pool / (p["shares"] + pref["count"] * ratio)
                for ratio in [pref["min_ratio"], pref["max_ratio"]]
            ]
        ),
        "preferred_dividend_pv": dividend_pv,
        "dividend_schedule": dividend_rows,
        "terminal_share": terminal_pv / enterprise if enterprise > 0 else None,
        "upside": price / p["market_price"] - 1 if pool > 0 else None,
        "warning": "股权残值非正，不能解释为负交易价格。" if pool <= 0 else None,
        "transition": {
            "balance_date": p["balance_date"],
            "valuation_date": p["valuation_date"],
            "stub_end": p["stub_end"],
            "elapsed_fraction": elapsed,
            "remaining_fraction": remaining,
            "full_year_company_fcf": s["fy_company_fcf"],
            "ytd_company_fcf": p["ytd_company_fcf"],
            "q4_company_fcf": q4_fcf,
            "estimated_elapsed_fcf": q4_fcf * elapsed,
            "paid_preferred_dividends": p["paid_preferred_dividends"],
            "rolled_cash": rolled_cash,
            "remaining_fcff": q4_fcff * remaining,
            "remaining_pv": stub_pv,
            "note": "已披露前三季度 FCF 不再折现；季末至估值日按天数估计净现金变化，估值日后仅计入剩余过渡期 FCFF。季度内现金流并非实际均匀发生，月度数据未披露。",
        },
    }


def reverse_cashflow(p, s):
    """Solve a common multiplier for company-FCF forecasts, not for past cash."""

    def evaluate(multiplier):
        scenario = deepcopy(s)
        scenario["company_fcf"] = [x * multiplier for x in s["company_fcf"]]
        try:
            return forecast_cashflow(p, scenario)["price"]
        except ValueError:
            return None

    samples = [(i / 100, evaluate(i / 100)) for i in range(501)]
    valid = [(x, y) for x, y in samples if y is not None]
    brackets = [
        (a[0], b[0])
        for a, b in zip(valid, valid[1:], strict=False)
        if a[1] <= p["market_price"] <= b[1]
    ]
    if not brackets:
        return {"status": "no_solution", "reason": "公司 FCF 预测的 0 至 5 倍范围内无可行解"}
    lo, hi = brackets[0]
    for _ in range(70):
        mid = (lo + hi) / 2
        if evaluate(mid) < p["market_price"]:
            lo = mid
        else:
            hi = mid
    scale = (lo + hi) / 2
    return {
        "status": "solved",
        "fcf_multiplier": scale,
        "implied_company_fcf": [x * scale for x in s["company_fcf"]],
        "reproduced_price": evaluate(scale),
        "target_price": p["market_price"],
        "basis": "固定过渡期、WACC、利息、股权薪酬、税率与转股条件，仅同比例放大未来各年公司口径 FCF。隐含要求不是管理层指引。",
    }

"""Source-derived mandatory preferred instruments; no issuer-specific parameters.

Fully diluted static allocation at the maximum contractual conversion ratio is
an explicit conservative approximation, not a priced conversion option model.
"""

import calendar
import re
from copy import deepcopy
from datetime import date


def compile_capital(spec, sources, as_of, balance_date):
    from tradingagents.web.automatic_valuation import check_quote
    from tradingagents.web.valuation import numeric

    if not isinstance(spec, dict) or spec.get("method") != "mandatory_conversion_max":
        raise ValueError("资本结构须使用已支持的强制转股条款模型")
    if (
        spec.get("principal_in_other_claims") is not False
        or spec.get("principal_in_debt") is not False
    ):
        raise ValueError("转股本金不得重复计入债务或其他权益要求")
    if spec.get("cash_dividends_in_fcff") is not False:
        raise ValueError("优先股股息在权益桥扣除，不得再次从 FCFF 扣除")
    if not isinstance(spec.get("assumptions"), str) or len(spec["assumptions"]) < 30:
        raise ValueError("须说明现金股息、最大转股、无提前转换/基本面变更及不计 capped call 的假设")
    groups = spec.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= 12:
        raise ValueError("缺少已披露优先股系列")
    day, balance = date.fromisoformat(as_of), date.fromisoformat(balance_date)
    if balance > day:
        raise ValueError("资本结构财务基准晚于估值日")

    def number(term, label, low=0, high=1e15):
        cited = check_quote(term, sources, as_of)
        amount = numeric(term.get("amount"), label, low, high)
        scale = term.get("scale", 1)
        if scale not in (1, 1000, 1e6, 1e9) or isinstance(scale, bool):
            raise ValueError("条款数值单位无效：" + label)
        numbers = [
            float(n.replace(",", "")) for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", cited)
        ]
        if not any(abs(v - amount) < 1e-8 for v in numbers):
            raise ValueError("条款数值不在引文中：" + label)
        return amount * scale

    def dated(term):
        cited = check_quote(term, sources, as_of)
        d = date.fromisoformat(term["date"])
        patterns = [
            d.isoformat(),
            f"{calendar.month_name[d.month]} {d.day}, {d.year}",
            f"{calendar.month_name[d.month]} {d.day} {d.year}",
        ]
        if not any(p.casefold() in cited.casefold() for p in patterns):
            raise ValueError("条款日期不在引文中：" + str(d))
        return d

    schedules, rows, used, quantities = [], [], set(), set()
    for group in groups:
        name = group.get("name")
        if not isinstance(name, str) or not name or name in used:
            raise ValueError("优先股组名称为空或重复")
        used.add(name)
        quantity_ref = group["depositary_shares"]
        identity = (
            quantity_ref["source_id"],
            " ".join(quantity_ref["quote"].split()),
            quantity_ref["amount"],
            quantity_ref.get("scale", 1),
        )
        if identity in quantities:
            raise ValueError("同一存托发行数量不能重复用于多个发行组")
        quantities.add(identity)
        count = number(quantity_ref, "存托份额", 1)
        denominator = number(group["receipts_per_preferred"], "每优先股对应存托份额", 1, 100000)
        liquidation = number(group["liquidation_per_preferred"], "每股清算优先额", 0.001)
        coupon = number(group["coupon_percent"], "年股息百分数", 0, 50) / 100
        conversion = dated(group["conversion_date"])
        first = dated(group["first_dividend_date"])
        if not first <= balance <= day < conversion or (conversion - day).days > 36525:
            raise ValueError(
                "须核实首次股息及未来转股日期；首次不规则股息尚未发生或已转股需另建口径"
            )
        evidence = check_quote(group["payment_schedule"], sources, as_of)
        months, dom = group["payment_schedule"]["months"], group["payment_schedule"]["day"]
        if (
            not isinstance(months, list)
            or len(months) not in (1, 2, 4, 12)
            or any(type(m) is not int or not 1 <= m <= 12 for m in months)
            or months != sorted(set(months))
            or type(dom) is not int
            or not 1 <= dom <= 28
        ):
            raise ValueError("股息月份或日数无效")
        if any(
            calendar.month_name[m].casefold() not in evidence.casefold() for m in months
        ) or not re.search(rf"\b{dom}\b", evidence):
            raise ValueError("股息月份和日数必须在原文中")
        if any(
            (months[(i + 1) % len(months)] - m) % 12 != 12 // len(months) % 12
            for i, m in enumerate(months)
        ):
            raise ValueError("暂仅支持等间隔固定股息")
        if conversion.month not in months or conversion.day != dom:
            raise ValueError("转股日非标准付息日，须补齐末次不规则股息")
        rate = numeric(group.get("dividend_discount_rate"), "股息折现率", 0, 0.5)
        if len(group.get("discount_reason", "")) < 10:
            raise ValueError("缺少独立股息折现率依据")
        series = group.get("series")
        if not isinstance(series, list) or not series:
            raise ValueError("缺少各系列转股区间")
        if len(series) > 1:
            allocation = check_quote(group["equal_allocation"], sources, as_of)
            if not re.search(r"evenly|equally|equal amounts|equal proportions", allocation, re.I):
                raise ValueError("多系列等额分配须有明确原文，不得假设均分")
        total_preferred = count / denominator
        names = set()
        for series_row in series:
            label = series_row["name"]
            if not label or label in names:
                raise ValueError("重复优先股系列")
            names.add(label)
            low = number(series_row["conversion_min"], "最低转股比例", 0.000001)
            high = number(series_row["conversion_max"], "最高转股比例", 0.000001)
            if low > high:
                raise ValueError("转股比例上下限倒置")
            quantity = total_preferred / len(series)
            rows.append(
                {
                    "group": name,
                    "series": label,
                    "preferred_shares": quantity,
                    "conversion_min": low,
                    "conversion_max": high,
                    "conversion_shares": quantity * high,
                    "conversion_date": conversion.isoformat(),
                }
            )
        payment = total_preferred * liquidation * coupon / len(months)
        for year in range(balance.year, conversion.year + 1):
            for month in months:
                d = date(year, month, dom)
                if max(balance, first) < d <= conversion:
                    schedules.append(
                        {
                            "group": name,
                            "date": d.isoformat(),
                            "amount": payment,
                            "status": "scheduled_since_balance" if d <= day else "future",
                            "pv": payment
                            if d <= day
                            else payment / (1 + rate) ** ((d - day).days / 365.25),
                        }
                    )
    return {
        "method": spec["method"],
        "valuation_date": as_of,
        "balance_date": balance_date,
        "terms": deepcopy(spec),
        "series": rows,
        "dividends": schedules,
        "conversion_shares": sum(r["conversion_shares"] for r in rows),
        "elapsed_dividends": sum(
            r["pv"] for r in schedules if r["status"] == "scheduled_since_balance"
        ),
        "future_dividend_pv": sum(r["pv"] for r in schedules if r["status"] == "future"),
        "assumptions": spec["assumptions"],
    }


def validate_capital(p):
    """Normalize numerical audit fields across Python/browser JSON, reject malformed bridge."""
    from tradingagents.web.valuation import numeric

    b = p["capital_structure"]
    if b.get("method") != "mandatory_conversion_max" or p.get("research_bridge"):
        raise ValueError("资本结构模型不支持或重复叠加权益桥")
    if b["valuation_date"] != p.get("valuation_date") or b["balance_date"] != p.get("balance_date"):
        raise ValueError("资本结构日期不匹配")

    def canonical_numbers(value):
        if isinstance(value, dict):
            return {k: canonical_numbers(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical_numbers(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    b["terms"] = canonical_numbers(b["terms"])
    for key in ("conversion_shares", "elapsed_dividends", "future_dividend_pv"):
        b[key] = numeric(b[key], key, 0, 1e15)
    for row in b["series"]:
        for key in ("preferred_shares", "conversion_min", "conversion_max", "conversion_shares"):
            row[key] = numeric(row[key], key, 0, 1e15)
    for row in b["dividends"]:
        for key in ("amount", "pv"):
            row[key] = numeric(row[key], key, 0, 1e15)
    return p


def adjust(p, result):
    b = p["capital_structure"]
    pool = result["equity_value"] - b["elapsed_dividends"] - b["future_dividend_pv"]
    common = p["shares"] * p["dilution"]
    effective = common + b["conversion_shares"]
    price = pool / effective
    result.update(
        equity_pool=pool,
        effective_shares=effective,
        equity_value=price * common,
        price=price,
        upside=price / p["market_price"] - 1 if p.get("market_price") and pool > 0 else None,
        warning="优先股股息及摊薄调整后普通股残值为负，不是可交易的负股价。"
        if pool < 0
        else result.get("warning"),
        capital_adjustment={
            "conversion_shares": b["conversion_shares"],
            "elapsed_dividends": b["elapsed_dividends"],
            "future_dividend_pv": b["future_dividend_pv"],
        },
    )
    return result

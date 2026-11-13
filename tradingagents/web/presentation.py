"""User-facing report labels; internal evidence IDs remain only in link targets."""

import re

LABELS = {
    "longName": ("公司名称", "识别研究对象"),
    "sector": ("板块", "供应商行业分类"),
    "industry": ("行业", "供应商行业分类"),
    "fullTimeEmployees": ("员工人数", "公司规模参考"),
    "totalRevenue": ("营业收入（TTM）", "最近十二个月收入"),
    "netIncomeToCommon": ("归属普通股股东净利润", "最近十二个月盈利"),
    "returnOnEquity": ("净资产收益率 ROE", "盈利与净资产的关系，注意杠杆影响"),
    "returnOnAssets": ("总资产收益率 ROA", "盈利与资产规模的关系"),
    "grossMargins": ("毛利率", "收入扣除营业成本后的比例"),
    "operatingMargins": ("经营利润率", "经营利润占收入比例"),
    "profitMargins": ("净利率", "净利润占收入比例"),
    "revenueGrowth": ("收入增长率", "供应商最近报告期的增长口径"),
    "earningsGrowth": ("利润增长率", "供应商最近报告期的增长口径"),
    "trailingPE": ("市盈率 PE（TTM）", "价格与近十二个月每股盈利之比"),
    "forwardPE": ("预期市盈率", "价格与预测盈利之比，不是已实现业绩"),
    "priceToBook": ("市净率 PB", "价格与每股净资产之比"),
    "currentRatio": ("流动比率", "流动资产与流动负债的关系"),
    "debtToEquity": ("债务权益比", "供应商以百分数提供"),
    "freeCashflow": ("自由现金流", "供应商现金流口径"),
    "marketCap": ("总市值", "交易币种下的市场规模"),
    "currentPrice": ("当前股价", "当前市场价格"),
    "trailingEps": ("每股盈利 EPS（TTM）", "最近十二个月每股盈利"),
    "Total Revenue": ("营业收入", "财报收入科目"),
    "Operating Revenue": ("营业收入", "财报收入科目"),
    "Gross Profit": ("毛利润", "收入减营业成本"),
    "Operating Income": ("经营利润", "经营活动利润"),
    "Net Income": ("净利润", "利润表净利润科目"),
    "Cost Of Revenue": ("营业成本", "利润表营业成本科目"),
    "Total Assets": ("总资产", "资产负债表期末资产"),
    "Stockholders Equity": ("股东权益", "资产负债表期末权益"),
    "Current Assets": ("流动资产", "资产负债表期末流动资产"),
    "Current Liabilities": ("流动负债", "资产负债表期末流动负债"),
    "Operating Cash Flow": ("经营活动现金流", "现金流量表科目，注意是否累计值"),
    "Capital Expenditure": ("资本开支", "Yahoo 通常以流出负值记录"),
    "Free Cash Flow": ("自由现金流", "现金流量表供应商口径"),
}
LABELS.update(
    {
        "country": ("国家或地区", "公司注册或经营所在地资料"),
        "city": ("城市", "公司总部所在地"),
        "website": ("公司网站", "供应商提供的官方网站"),
        "longBusinessSummary": ("业务简介", "公司主营业务描述"),
        "currency": ("交易币种", "股价及市值的报价币种"),
        "financialCurrency": ("财报币种", "财务报表使用的报告币种"),
        "heldPercentInsiders": ("内部人持股比例", "供应商概览，不是内部人交易记录"),
        "heldPercentInstitutions": ("机构持股比例", "供应商概览，不代表完整股东名册"),
        "sharesOutstanding": ("流通在外股数", "供应商当前股本口径"),
        "mostRecentQuarter": ("最近财报期", "供应商最近报告的财务期末日期"),
        "totalCash": ("现金及等价物", "供应商当前财务概览口径"),
        "totalDebt": ("总债务", "供应商当前财务概览口径"),
        "dividendYield": ("股息率", "供应商以百分数提供"),
        "phone": ("联系电话", "公司公开联系方式"),
        "address1": ("办公地址", "公司公开地址"),
        "zip": ("邮政编码", "公开地址资料"),
        "exchange": ("上市交易所", "证券挂牌市场"),
    }
)
RATIOS = {
    "returnOnEquity",
    "returnOnAssets",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
    "revenueGrowth",
    "earningsGrowth",
    "heldPercentInsiders",
    "heldPercentInstitutions",
}


def readable_evidence(snapshot):
    profile = snapshot.get("company", {}).get("profile", {})
    rows = []
    for entry in snapshot.get("evidence", []):
        field = entry["field"]
        value = entry.get("value")
        label, explanation = LABELS.get(
            field, (field.replace("_", " "), "供应商原始科目；请结合报告期间与完整财报解读")
        )
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            if field == "mostRecentQuarter":
                from datetime import datetime, timezone

                display = datetime.fromtimestamp(value, timezone.utc).date().isoformat()
            elif field in RATIOS:
                display = f"{value * 100:,.2f}%"
            elif field in ("debtToEquity", "dividendYield"):
                display = f"{value:,.2f}%"
            else:
                display = f"{value:,.2f}".rstrip("0").rstrip(".")
                if ("Shares" in field or "Share Count" in field) and "Per Share" not in field:
                    display += " 股"
                elif "EPS" in field or "Per Share" in field:
                    display += (
                        " "
                        + (entry.get("currency") or profile.get("financialCurrency") or "报告币种")
                        + "/股"
                    )
                elif field == "Tax Rate For Calcs":
                    display = f"{value * 100:,.2f}%"
                elif entry.get("currency"):
                    display += " " + entry["currency"] + "（保留供应商科目单位）"
                elif field in (
                    "totalRevenue",
                    "netIncomeToCommon",
                    "freeCashflow",
                    "totalCash",
                    "totalDebt",
                ):
                    display += " " + (profile.get("financialCurrency") or "币种未提供")
                elif field in ("marketCap", "currentPrice", "trailingEps"):
                    display += " " + (profile.get("currency") or "币种未提供")
        else:
            display = str(value) if value is not None else "未提供"
        period = entry.get("period", "")
        if period == "current snapshot":
            period = "当前资料（采集时点）"
        elif entry.get("frequency"):
            period = ("年度 · " if entry["frequency"] == "annual" else "季度 · ") + period
        rows.append(
            {
                "id": entry["id"],
                "field": field,
                "label": label,
                "period": period,
                "display_value": display,
                "source": entry.get("source") or "供应商资料",
                "explanation": explanation,
            }
        )
    return rows


def present_report(text, snapshot=None):
    """Keep archived originals intact; hide trace IDs in the presentation layer."""
    snapshot = snapshot or {}
    rows = {r["id"]: r for r in readable_evidence(snapshot)}

    def citation(match):
        ref = match.group(2)
        row = rows.get(ref)
        label = (
            f"{row['label']} · {row['period']} · {row['display_value']}" if row else "查看财务依据"
        )
        label = label.replace("[", "(").replace("]", ")").replace("\n", " ")
        return f"[{label}](#evidence-{ref})"

    text = re.sub(r"\[([^\]]*)\]\(#evidence-([\w.-]+)\)", citation, text or "")
    text = re.sub(
        r"(?:快照编号|快照|snapshot[_ ]?id)\s*[:：]\s*[^\s·；;\n]+\s*[·；;]?\s*",
        "",
        text,
        flags=re.I,
    )
    if snapshot.get("id"):
        text = text.replace(str(snapshot["id"]), "本次研究数据")
    text = re.sub(r"\b[a-f0-9]{32,64}\b", "本次研究数据", text)
    for ref, row in sorted(rows.items(), key=lambda p: -len(p[0])):
        text = re.sub(
            r"(?<![\w.#-])" + re.escape(ref) + r"(?![\w.-])",
            lambda m: f"{row['label']}（{row['period']}：{row['display_value']}）",
            text,
        )
    return re.sub(r"(?<![/\w])TradingAgents?\b", "FundAnalyst", text, flags=re.I)

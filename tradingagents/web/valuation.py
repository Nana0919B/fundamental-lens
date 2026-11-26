"""Auditable, snapshot-only FCFF valuation. No network or model-generated arithmetic."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime

VERSION = "fcff-2.1"
SCENARIOS = ("bear", "base", "bull")
ASSUMPTION_POLICY = "research-defaults-1.0"
SCHEMA_VERSION = 1


def numeric(value, label, low=-1e15, high=1e15):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须为有限数值")
    if not low <= value <= high:
        raise ValueError(f"{label} 必须在 {low} 至 {high} 之间")
    return float(value)


def cell(company, kind, names):
    table = company.get("statements", {}).get("annual", {}).get(kind, {})
    # Never jump to an older value silently.
    for name in names:
        row = next((r for r in table.get("rows", []) if r["name"] == name), None)
        if row and row.get("values") and row["values"][0] is not None:
            return {
                "value": row["values"][0],
                "field": name,
                "period": table["periods"][0],
                "source": company.get("source"),
                "basis": "annual/" + kind,
            }
    return {"value": None, "field": names[0], "period": None, "source": company.get("source")}


def prepare(job):
    company = (job.get("research_snapshot") or {}).get("company") or job.get("data") or {}
    profile = company.get("profile", {})
    evidence = {
        k: cell(company, kind, names)
        for k, kind, names in [
            ("revenue", "income", ["Total Revenue"]),
            ("ebit", "income", ["Operating Income"]),
            (
                "da",
                "cashflow",
                ["Depreciation And Amortization", "Depreciation Amortization Depletion"],
            ),
            ("capex", "cashflow", ["Capital Expenditure"]),
        ]
    }
    for key, field in [
        ("cash", "totalCash"),
        ("debt", "totalDebt"),
        ("shares", "sharesOutstanding"),
        ("market_price", "currentPrice"),
    ]:
        evidence[key] = {
            "value": profile.get(field),
            "field": field,
            "period": company.get("retrieved_at"),
            "source": company.get("source"),
            "basis": "profile snapshot",
        }
    missing = [k for k in ("revenue", "cash", "debt", "shares") if evidence[k]["value"] is None]
    currency = profile.get("financialCurrency")
    if not currency or currency != profile.get("currency"):
        missing.append("财报与报价币种必须相同；暂不自动换汇")
    if profile.get("sector") == "Financial Services":
        missing.append("金融机构不适用此经营企业 FCFF 模型")
    revenue = evidence["revenue"]["value"]
    margin = (
        evidence["ebit"]["value"] / revenue
        if revenue and evidence["ebit"]["value"] is not None
        else None
    )
    # Every forecast is a visibly named editable assumption, never a claimed forecast fact.
    scenarios = {}
    for name, growth, target, wacc in [
        ("bear", 0.02, 0.04, 0.12),
        ("base", 0.05, 0.08, 0.10),
        ("bull", 0.08, 0.12, 0.09),
    ]:
        start = margin if margin is not None else 0
        scenarios[name] = {
            "growth": [growth] * 5,
            "margins": [start + (target - start) * (i + 1) / 5 for i in range(5)],
            "da_ratio": 0.025,
            "capex_ratio": 0.035,
            "nwc_ratio": 0.10,
            "tax_rate": 0.25,
            "wacc": wacc,
            "terminal_growth": 0.02,
        }
    inputs = {
        k: evidence[k]["value"] for k in ("revenue", "cash", "debt", "shares", "market_price")
    }
    inputs.update(
        method="operating_fcff",
        schema_version=SCHEMA_VERSION,
        assumption_policy=ASSUMPTION_POLICY,
        operating_cash=0,
        other_claims=profile.get("valuationOtherClaims"),
        dilution=1.0,
        years=5,
        forecast_start=date.fromisoformat(job.get("date") or date.today().isoformat()).year + 1,
        rationale="",
        scenarios=scenarios,
    )
    warnings = [
        "预填参数是可编辑的研究假设，不是分析师共识或模型已证实的预测。WACC 为直接假设，未自动估计 CAPM。",
        "现金与债务使用同一供应商概览；现金默认全部可分配，请填写经营必需现金，核对受限现金、租赁及其他负债口径。",
        "其他权益要求必须明确填写；优先股账面面值不能代替经济价值，可转债/优先股应选择扣除或转股摊薄，避免重复计算。",
        "股数是快照发行在外股数；摊薄倍数为显式假设，不使用历史加权平均股数替代。",
        "预测从下一完整年度开始，以当前年度末为简化估值基准；未建模剩余年度过渡现金流。价格和财报是归档数据，不是实时行情。",
        "终值沿用最后一年利润率与再投资比例；永续增长必须小于 WACC。亏损期不自动确认税盾。",
    ]
    if margin is not None and margin < 0:
        warnings.append("基期营业亏损：采用逐年利润率恢复假设；恢复速度和终值依赖度需重点复核。")
    context = job.get("historical_context")
    if context:
        as_of = context["as_of_date"]
        if as_of != job.get("date"):
            raise ValueError("历史估值日与研究快照不一致")
        for filing in context["source_ledger"]:
            if filing["filed"] >= as_of:
                raise ValueError("历史输入含尚未公开的财报")
            for fact in filing["facts"].values():
                if fact.get("fx_date", "0001-01-01") >= as_of:
                    raise ValueError("历史汇率必须早于估值日")
                if fact["filed"] >= as_of or fact["end"] > as_of:
                    raise ValueError("历史原始证据超出估值截止日")
        inputs.update(
            other_claims=profile.get("valuationOtherClaims") or 0,
            rationale="研究页统一默认情景：收入增长2%/5%/8%，利润率五年过渡至4%/8%/12%；WACC12%/10%/9%，永续增长2%。"
            "现金全部计入，其他权益要求假设为0、摊薄倍数1；均为可复核的研究假设，并非历史事实。"
            "历史数据仅使用当日之前披露的原始年报，不复用今天的新闻或AI研究结论。",
        )
        if profile.get("valuationOtherClaims") is not None:
            inputs["rationale"] = inputs["rationale"].replace(
                "其他权益要求假设为0", "其他权益要求按原始证据账面值试验性扣减"
            )
        if context.get("forecast_template"):
            template = validate_forecast_template(context["forecast_template"])
            if template["as_of_date"] > as_of or template["ticker"] != job["ticker"]:
                raise ValueError("研究模板晚于历史日期或股票不一致")
            inputs.update(
                years=template["years"],
                scenarios=deepcopy(template["scenarios"]),
                rationale=template["rationale"],
            )
            warnings.append(
                "使用已保存的历史研究预测模板 "
                + template["input_digest"]
                + "；模板定义于今天，属于事后策略实验；实际财务数据仍逐期更新。"
            )
        warnings.extend(context.get("source_warnings", []))
        warnings.extend(
            [
                context["note"],
                (
                    "其他权益要求为已披露账面值代理，未按市场经济价值核定；同研究页可编辑。"
                    if profile.get("valuationOtherClaims") is not None
                    else "其他权益要求0、现金全额可分配、摊薄倍数1为统一默认假设，未逐公司核验附注；同研究页可编辑。"
                ),
                "历史每股单位已还原至估值当日；年度预测仍使用研究页的整年折现近似，未建模过渡年度。",
            ]
        )
        evidence["historical_cutoff"] = {
            "value": None,
            "field": "filed < " + as_of,
            "period": as_of,
            "source": company.get("source", "original filings"),
        }
    return {
        "version": VERSION,
        "currency": currency,
        "evidence": evidence,
        "missing": missing,
        "warnings": warnings,
        "inputs": inputs,
        "snapshot_digest": job.get("snapshot_digest"),
        "retrieved_at": company.get("retrieved_at"),
        "saved": job.get("valuation"),
    }


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("估值输入必须是对象")
    p = deepcopy(payload)
    if (
        p.get("method", "operating_fcff") != "operating_fcff"
        or p.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION
    ):
        raise ValueError("不支持的经营估值方法或假设结构版本")
    p.setdefault("method", "operating_fcff")
    p.setdefault("schema_version", SCHEMA_VERSION)
    n = p.get("years")
    if isinstance(n, bool) or not isinstance(n, int) or not 3 <= n <= 10:
        raise ValueError("预测期须为 3 至 10 年")
    for key in ("revenue", "shares"):
        p[key] = numeric(p.get(key), key, 1, 1e15)
    for key in ("cash", "debt", "operating_cash", "other_claims"):
        p[key] = numeric(p.get(key), key, 0, 1e15)
    if p["operating_cash"] > p["cash"]:
        raise ValueError("经营必需现金不能超过现金总额")
    p["dilution"] = numeric(p.get("dilution"), "摊薄倍数", 1, 10)
    if p.get("market_price") is not None:
        p["market_price"] = numeric(p["market_price"], "参考股价", 0.000001, 1e9)
    start = p.get("forecast_start")
    if isinstance(start, bool) or not isinstance(start, int) or not 2000 <= start <= 2200:
        raise ValueError("预测起始年度无效")
    if not isinstance(p.get("rationale"), str) or not 10 <= len(p["rationale"].strip()) <= 6000:
        raise ValueError("请填写至少 10 字的假设依据，说明恢复路径、WACC 及其他权益调整")
    if not isinstance(p.get("scenarios"), dict) or set(p["scenarios"]) != set(SCENARIOS):
        raise ValueError("必须包含悲观、基准、乐观三种情景")
    for name in SCENARIOS:
        s = p["scenarios"][name]
        if not isinstance(s, dict):
            raise ValueError("情景格式无效")
        for key, lo, hi in [("growth", -0.9, 1), ("margins", -1, 1)]:
            values = s.get(key)
            if not isinstance(values, list) or len(values) != n:
                raise ValueError(f"{name}/{key} 必须包含 {n} 个逐年参数")
            s[key] = [numeric(v, key, lo, hi) for v in values]
        for key, lo, hi in [
            ("da_ratio", 0, 1),
            ("capex_ratio", 0, 1),
            ("nwc_ratio", 0, 2),
            ("tax_rate", 0, 0.6),
            ("wacc", 0.01, 0.5),
            ("terminal_growth", -0.05, 0.08),
        ]:
            s[key] = numeric(s.get(key), key, lo, hi)
        if s["wacc"] - s["terminal_growth"] < 0.005 - 1e-12:
            raise ValueError("WACC 必须至少高于永续增长率 0.5 个百分点")
    return p


def project(p, s):
    revenue = p["revenue"]
    rows = []
    for index, (growth, margin) in enumerate(zip(s["growth"], s["margins"], strict=False)):
        previous = revenue
        revenue *= 1 + growth
        ebit = revenue * margin
        tax = max(ebit, 0) * s["tax_rate"]
        da, capex = revenue * s["da_ratio"], revenue * s["capex_ratio"]
        delta_nwc = (revenue - previous) * s["nwc_ratio"]
        fcff = ebit - tax + da - capex - delta_nwc
        pv = fcff / (1 + s["wacc"]) ** (index + 1)
        rows.append(
            {
                "year": p["forecast_start"] + index,
                "revenue": revenue,
                "ebit": ebit,
                "tax": tax,
                "da": da,
                "capex": capex,
                "delta_nwc": delta_nwc,
                "fcff": fcff,
                "pv": pv,
            }
        )
    terminal_revenue = revenue * (1 + s["terminal_growth"])
    terminal_ebit = terminal_revenue * s["margins"][-1]
    terminal_fcff = (
        terminal_ebit
        - max(terminal_ebit, 0) * s["tax_rate"]
        + terminal_revenue * (s["da_ratio"] - s["capex_ratio"])
        - (terminal_revenue - revenue) * s["nwc_ratio"]
    )
    if terminal_fcff <= 0:
        raise ValueError("稳定期 FCFF 非正，无法使用持续经营永续增长终值；请复核恢复假设")
    terminal = terminal_fcff / (s["wacc"] - s["terminal_growth"])
    terminal_pv = terminal / (1 + s["wacc"]) ** p["years"]
    enterprise = sum(r["pv"] for r in rows) + terminal_pv
    equity = enterprise + p["cash"] - p["operating_cash"] - p["debt"] - p["other_claims"]
    price = equity / (p["shares"] * p["dilution"])
    return {
        "rows": rows,
        "terminal_fcff": terminal_fcff,
        "terminal_pv": terminal_pv,
        "enterprise_value": enterprise,
        "equity_value": equity,
        "price": price,
        "terminal_share": terminal_pv / enterprise if enterprise > 0 else None,
        "upside": price / p["market_price"] - 1 if p.get("market_price") and equity > 0 else None,
        "warning": "股权残值为负：不是可交易的负股价，提示该组假设下普通股缺乏价值支持。"
        if equity < 0
        else None,
    }


def reverse(p, s):
    """Solve a parallel margin-path shift; usable even with negative starting FCFF."""
    if not p.get("market_price"):
        return {"status": "unavailable", "reason": "缺少参考股价"}
    target = p["market_price"]

    def evaluate(shift):
        scenario = deepcopy(s)
        scenario["margins"] = [v + shift for v in s["margins"]]
        try:
            return project(p, scenario)["price"]
        except ValueError:
            return None

    lower, upper = -0.5 - min(s["margins"]), 0.6 - max(s["margins"])
    # Find a valid bracket; invalid perpetual cash flows are not replaced by zeros.
    samples = [(lower + (upper - lower) * i / 400) for i in range(401)]
    valid = [(x, evaluate(x)) for x in samples]
    valid = [(x, y) for x, y in valid if y is not None]
    bracket = next(
        ((a[0], b[0]) for a, b in zip(valid, valid[1:], strict=False) if a[1] <= target <= b[1]),
        None,
    )
    if bracket is None:
        return {
            "status": "no_solution",
            "reason": "营业利润率 -50% 至 60% 的搜索范围内无可行解；不能解释为股票便宜或昂贵",
        }
    lo, hi = bracket
    for _ in range(70):
        mid = (lo + hi) / 2
        if evaluate(mid) < target:
            lo = mid
        else:
            hi = mid
    shift = (lo + hi) / 2
    return {
        "status": "solved",
        "margin_shift": shift,
        "implied_margins": [v + shift for v in s["margins"]],
        "implied_terminal_margin": s["margins"][-1] + shift,
        "reproduced_price": evaluate(shift),
        "target_price": target,
        "basis": "固定基准收入增长、再投资、税率、WACC、股数与终值增长，仅平移全部预测年度营业利润率。隐含要求不是预测。",
    }


def calculate(job, payload):
    prepared = prepare(job)
    if prepared["missing"]:
        raise ValueError("数据不完整或模型不适用：" + "、".join(prepared["missing"]))
    if isinstance(payload, dict) and payload.get("method") == "company_fcf_bridge":
        from tradingagents.web.valuation_cashflow import (
            forecast_cashflow,
            reverse_cashflow,
            validate_cashflow,
        )

        p = validate_cashflow(payload)
        project_fn, reverse_fn = forecast_cashflow, reverse_cashflow
    else:
        p = validate(payload)
        project_fn, reverse_fn = project, reverse
    historical_context = deepcopy(job.get("historical_context"))
    if historical_context:
        for key in ("revenue", "cash", "debt", "shares", "market_price", "forecast_start"):
            if p.get(key) != prepared["inputs"].get(key):
                raise ValueError("历史事实输入不可改写：" + key + "；仅可调整预测假设")
        if p != prepared["inputs"]:
            historical_context["assumption_status"] = "edited_retrospective_assumptions"
    results = {key: project_fn(p, p["scenarios"][key]) for key in SCENARIOS}
    s = p["scenarios"]["base"]
    waccs = [s["wacc"] + d for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    growths = [s["terminal_growth"] + d for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    grid = []
    for w in waccs:
        row = []
        for g in growths:
            if w <= 0 or w - g < 0.005 - 1e-12:
                row.append(None)
                continue
            try:
                row.append(project_fn(p, {**s, "wacc": w, "terminal_growth": g})["price"])
            except ValueError:
                row.append(None)
        grid.append(row)
    warnings = list(prepared["warnings"])
    if p.get("method") == "company_fcf_bridge":
        warnings = [
            "历史事实、管理层目标、研究者假设分别列示；DCF 给出条件估值，不是已发生的未来现金流。",
            "过渡期包含已披露累计现金流、季度预测和按日估计的期内分配；实际现金流通常集中在交付/预收款日期。",
            "公司 CFO−资本开支加税后净融资利息，减股权薪酬现金等价成本，得到调整后 FCFF；不可把公司 FCF 直接当 FCFF。",
            "采用强制转股后股数分配权益，并扣除剩余现金股息现值，不再扣优先股清算本金。假设正常强制转股、无提前转换或基本面变更事件。",
            "股票支付股息、反摊薄调整或新增融资会改变股数；此处假设剩余股息以现金支付。未来员工股权成本已从现金流扣除，不再重复加股数。",
            "发行在外股数采用最新公开披露。现有普通股价值=每股价值×当前普通股数；转股后权益池还包含优先股持有人的权益。",
            "基准现金流包含运营养老金缴款，不再重复扣全额养老金缺口；额外资金缺口可在其他权益要求中压力测试。",
            "显式年度使用年中折现；稳定期为保守年末永续现金流。终值现金流扣除暂时性营运资本释放，不能永久外推库存释放。",
            "敏感性保持优先股股息折现率独立不变；反向 DCF 只改变未来公司 FCF，不改历史或过渡期。",
        ]
    if any(r["terminal_share"] is None or r["terminal_share"] > 0.75 for r in results.values()):
        warnings.append("至少一个情景的终值占比超过 75% 或企业价值非正，估值高度依赖远期假设。")
    if not results["bear"]["price"] <= results["base"]["price"] <= results["bull"]["price"]:
        warnings.append("情景价格顺序与悲观/基准/乐观标签不一致，请复核参数。")
    canonical = json.dumps(p, sort_keys=True, allow_nan=False)
    return {
        "version": VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "snapshot_digest": prepared["snapshot_digest"],
        "input_digest": hashlib.sha256(canonical.encode()).hexdigest(),
        "currency": prepared["currency"],
        "inputs": p,
        "historical_context": historical_context,
        "evidence": prepared["evidence"],
        "scenarios": results,
        "sensitivity": {"waccs": waccs, "growths": growths, "prices": grid},
        "reverse": reverse_fn(p, s),
        "warnings": warnings,
    }


def validate_forecast_template(template):
    """Only forecast assumptions cross dates; financial facts never do."""
    if not isinstance(template, dict):
        raise ValueError("研究模板无效")
    required = {
        "as_of_date",
        "ticker",
        "source_job_id",
        "input_digest",
        "years",
        "scenarios",
        "rationale",
    }
    if set(template) != required:
        raise ValueError("研究模板字段无效")
    date.fromisoformat(template["as_of_date"])
    if not all(
        isinstance(template[k], str) and template[k]
        for k in ("ticker", "source_job_id", "input_digest")
    ):
        raise ValueError("研究模板来源无效")
    validate(
        {
            "years": template["years"],
            "revenue": 1,
            "shares": 1,
            "cash": 0,
            "debt": 0,
            "operating_cash": 0,
            "other_claims": 0,
            "dilution": 1,
            "forecast_start": 2020,
            "market_price": 1,
            "rationale": template["rationale"],
            "scenarios": template["scenarios"],
        }
    )
    return deepcopy(template)

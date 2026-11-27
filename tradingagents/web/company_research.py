"""Source-bound company research plan -> reproducible shared FCFF inputs.

The plan contains reviewed facts and explicit researcher judgments. It is not an
LLM's unconstrained numerical output and cannot be reused for an earlier date.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

PLAN = Path(__file__).resolve().parents[2] / "research/valuations/GOOGL-2026-11-27.json"


def build(job):
    if job.get("ticker") != "GOOGL" or not PLAN.exists():
        return None
    plan = json.loads(PLAN.read_text())
    if job.get("id") != plan["job_id"]:
        return None
    if (
        job.get("date") != plan["as_of_date"]
        or job.get("snapshot_digest") != plan["snapshot_digest"]
    ):
        raise ValueError("公司研究方案与研究日期/财务快照不匹配，需要重新核实")
    for key, expected in plan["report_sha256"].items():
        if hashlib.sha256(job.get("reports", {}).get(key, "").encode()).hexdigest() != expected:
            raise ValueError("公司研究结论已变化，需要重新生成估值方案")
    if any(s["filed"] >= job["date"] for s in plan["sources"]):
        raise ValueError("估值方案包含截止日之后的资料")
    f, a = plan["facts_millions"], plan["judgments"]
    cash = f["cash_equivalents"] + f["marketable_debt"]
    assets = f["marketable_equity"] + f["nonmarketable_equity"]
    debt = f["long_term_debt"] + f["current_notes"] + f["finance_leases"]
    claims = (
        f["nci_including_redeemable"]
        + f["deferred_tax_liability"]
        - f["deferred_tax_asset"]
        + f["past_income_tax_payable_noncurrent"]
    )
    shares = sum(f[k] for k in ["class_a_shares", "class_b_shares", "class_c_shares"])
    preferred = f["preferred_depositary_shares"] / 20
    conversion = (
        preferred
        / 2
        * (a["preferred_max_conversion_ratio_a"] + a["preferred_max_conversion_ratio_b"])
    )
    cases = {}
    for name, c in plan["scenarios"].items():
        q4s, q4c, q4o = (
            f["services_q3_revenue"] * c["q4_services_factor"],
            f["cloud_q3_revenue"] * c["q4_cloud_factor"],
            380,
        )
        q4r = q4s + q4c + q4o
        q4ebit = (
            q4s * c["q4_services_margin"]
            + q4c * c["q4_cloud_margin"]
            - 2100
            - q4r * c["q4_central_ratio"]
        )
        q4da = (f["da_9m_ppe"] + f["amortization_9m"]) / 3 * 1.15
        q4capex = c["fy_capex"] - f["capex_9m"]
        q4nwc = max(q4r - 127577, 0) * c["nwc_ratio"]
        q4fcff = q4ebit * (1 - a["tax_rate"]) + q4da - q4capex - q4nwc
        rs, rc, ro = (
            f["services_9m_revenue"] + q4s,
            f["cloud_9m_revenue"] + q4c,
            f["other_9m_revenue"] + q4o,
        )
        base = rs + rc + ro + f["hedge_9m_revenue"]
        previous, details = base, []
        for i in range(5):
            rs *= 1 + c["growth_services"][i]
            rc *= 1 + c["growth_cloud"][i]
            ro *= 1.10
            revenue = rs + rc + ro
            central = revenue * c["central_ratio"][i]
            ebit = (
                rs * c["margin_services"][i]
                + rc * c["margin_cloud"][i]
                - c["other_losses"][i]
                - central
            )
            details.append(
                {
                    "year": 2027 + i,
                    "services_revenue": rs * 1e6,
                    "cloud_revenue": rc * 1e6,
                    "other_revenue": ro * 1e6,
                    "central_cost": central * 1e6,
                    "other_loss": c["other_losses"][i] * 1e6,
                    "growth": revenue / previous - 1,
                    "margin": ebit / revenue,
                    "capex_ratio": c["capex"][i] / revenue,
                }
            )
            previous = revenue
        # Ten explicit years: do not jump straight from AI buildout to a cheap terminal.
        last_growth, last_margin = details[-1]["growth"], details[-1]["margin"]
        last_capex_ratio = details[-1]["capex_ratio"]
        stable_da = c["da_ratio"][-1]
        stable_capex = (
            stable_da
            + last_margin * (1 - a["tax_rate"]) * a["terminal_growth"] / c["roic"]
            - a["terminal_growth"] / (1 + a["terminal_growth"]) * c["nwc_ratio"]
        )
        for n in range(1, 6):
            growth = last_growth + (a["terminal_growth"] - last_growth) * n / 5
            rs *= 1 + growth
            rc *= 1 + growth
            ro *= 1 + growth
            revenue = rs + rc + ro
            details.append(
                {
                    "year": 2031 + n,
                    "services_revenue": rs * 1e6,
                    "cloud_revenue": rc * 1e6,
                    "other_revenue": ro * 1e6,
                    "central_cost": revenue * c["central_ratio"][-1] * 1e6,
                    "other_loss": c["other_losses"][-1] * 1e6,
                    "growth": growth,
                    "margin": last_margin,
                    "capex_ratio": last_capex_ratio + (stable_capex - last_capex_ratio) * n / 5,
                    "normalization": True,
                }
            )
        cases[name] = {
            "growth": [d["growth"] for d in details],
            "margins": [d["margin"] for d in details],
            "da_ratio": c["da_ratio"][0],
            "capex_ratio": details[0]["capex_ratio"],
            "da_ratios": c["da_ratio"] + [stable_da] * 5,
            "capex_ratios": [d["capex_ratio"] for d in details],
            "nwc_ratio": c["nwc_ratio"],
            "tax_rate": a["tax_rate"],
            "wacc": c["wacc"],
            "terminal_growth": a["terminal_growth"],
            "terminal_roic": c["roic"],
            "asset_haircut": c["haircut"],
            "revenue_base": base * 1e6,
            "quarter_fcff": q4fcff * 1e6,
            "quarter_net_interest": (1768 - 967) * 0.8 * 1e6,
            "segment_forecasts": details,
            "transition_basis": {
                "revenue": q4r * 1e6,
                "ebit": q4ebit * 1e6,
                "da": q4da * 1e6,
                "capex": q4capex * 1e6,
                "nwc": q4nwc * 1e6,
            },
        }
    basis = {
        "version": plan["version"],
        "sources": plan["sources"],
        "links": plan["research_links"],
        "facts_millions": f,
        "fact_locations": plan["fact_locations"],
        "judgments": a["notes"],
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "report_sha256": plan["report_sha256"],
    }
    inputs = {
        "method": "operating_fcff",
        "schema_version": 1,
        "assumption_policy": plan["version"],
        "years": 10,
        "forecast_start": 2027,
        "revenue": cases["base"]["revenue_base"],
        "cash": cash * 1e6,
        "non_operating_assets": assets * 1e6,
        "debt": debt * 1e6,
        "other_claims": claims * 1e6,
        "operating_cash": cases["base"]["revenue_base"] * 0.04,
        "shares": shares * 1e6,
        "dilution": 1.0,
        "market_price": 273.1000061035156,
        "market_price_date": "2026-11-25 (NASDAQ close, America/New_York)",
        "valuation_date": job["date"],
        "balance_date": "2026-09-30",
        "scenarios": cases,
        "research_basis": basis,
        "research_bridge": {
            "balance_date": "2026-09-30",
            "valuation_date": job["date"],
            "stub_end": "2026-12-31",
            "conversion_shares": conversion * 1e6,
            "preferred_quarter_dividend": preferred * 1000 * 0.0625 / 4 * 1e6,
            "preferred_paid_since_balance": preferred * 15.63 * 1e6,
            "preferred_discount_rate": 0.0625,
            "dividend_dates": [
                f"{y}-{m:02d}-15"
                for y in (2027, 2028, 2029)
                for m in (2, 5, 8, 11)
                if f"{y}-{m:02d}-15" <= "2029-05-15"
            ],
        },
        "rationale": "由本份经营研究的四个核查结论生成：分部增长、AI资本开支、投资重估收益隔离、股权价值桥。以已披露2026前三季度加Q4情景形成2026基数，预测2027–2036，其中2032–2036逐步向稳定增长及再投资率收敛，避免第五年资本开支悬崖。Services与Cloud分别设定增长和利润率，扣Other Bets亏损及集团AI费用；收入/利润率由分部汇总自动生成。已核实财务事实、管理层方向与研究者量化情景明确分开；未来增速并非公司指引。现金排除权益投资，股数合并A/B/C，优先股最大转股摊薄加剩余股息，不扣本金；资本开支、折旧逐年建模。未根据股票价格或是否成交调整假设。",
    }
    audit = {
        "version": plan["version"],
        "sector": "Alphabet：广告/订阅＋云/TPU",
        "periods": ["2026-09-30", "2025-12-31"],
        "observed_growth": [
            f["services_9m_revenue"] / f["services_9m_prior"] - 1,
            f["cloud_9m_revenue"] / f["cloud_9m_prior"] - 1,
        ],
        "observed_margins": [
            f["services_q3_ebit"] / f["services_q3_revenue"],
            f["cloud_q3_ebit"] / f["cloud_q3_revenue"],
        ],
    }
    warnings = a["notes"] + [
        "2026 Q4现金流、季度内现金变化和2027起预测属于研究估计；季末到估值日按天数分配，不代表实际每日现金流。",
        "投资按季度末账面/公允价值及情景折价，未取得估值日每项资产报价；受限SpaceX不当作现金。",
        "GAAP营业利润已包含员工股权薪酬，按经济现金成本处理；季度净现金滚动忽略未披露的融资、收购与营运资本异常，需后续披露更新。",
        "原研究文字及供应商快照保留；补充的SEC/业绩会证据用于本次估值，不自动改写原投资评级。",
    ]
    return {"inputs": inputs, "audit": audit, "warnings": warnings}


def validate_bridge(p):
    from tradingagents.web.valuation import numeric

    b = p["research_bridge"]
    start, day, end = [
        date.fromisoformat(b[k]) for k in ("balance_date", "valuation_date", "stub_end")
    ]
    if not start <= day < end or end.year + 1 != p["forecast_start"] or (end - start).days > 366:
        raise ValueError("公司研究过渡期日期无效")
    for k in ("conversion_shares", "preferred_quarter_dividend", "preferred_paid_since_balance"):
        b[k] = numeric(b[k], k, 0, 1e15)
    b["preferred_discount_rate"] = numeric(
        b["preferred_discount_rate"], "preferred_discount_rate", 0, 0.5
    )
    dates = [date.fromisoformat(d) for d in b["dividend_dates"]]
    if dates != sorted(set(dates)) or any(d <= day for d in dates):
        raise ValueError("剩余优先股股息日期无效")
    for s in p["scenarios"].values():
        for k in ("quarter_fcff", "quarter_net_interest"):
            s[k] = numeric(s[k], k, -1e15, 1e15)
        s["revenue_base"] = numeric(s["revenue_base"], "revenue_base", 1, 1e15)
        # JSON in the browser serializes 100.0 as 100; normalize audit-only
        # numeric columns too so an unchanged save retains its input digest.
        for row in s.get("segment_forecasts", []):
            for key in (
                "services_revenue",
                "cloud_revenue",
                "other_revenue",
                "central_cost",
                "other_loss",
                "capex_ratio",
                "growth",
                "margin",
            ):
                row[key] = numeric(row[key], key)
        for key, value in s.get("transition_basis", {}).items():
            s["transition_basis"][key] = numeric(value, key)

    return p


def adjust(p, s, result):
    """Apply dated stub and equity bridge to the SAME operating FCFF projection."""
    b = p["research_bridge"]
    start, day, end = [
        date.fromisoformat(b[k]) for k in ("balance_date", "valuation_date", "stub_end")
    ]
    fraction = (day - start).days / (end - start).days
    stub = (end - day).days / 365.25
    # Base project uses year-end discounting; move all annual flows to actual dates.
    factor = (1 + s["wacc"]) ** stub
    for row in result["rows"]:
        row["pv"] /= factor
    result["terminal_pv"] /= factor
    remaining = s["quarter_fcff"] * (1 - fraction)
    remaining_pv = remaining / (1 + s["wacc"]) ** (stub / 2)
    enterprise = sum(r["pv"] for r in result["rows"]) + result["terminal_pv"] + remaining_pv
    elapsed = (s["quarter_fcff"] + s["quarter_net_interest"]) * fraction - b[
        "preferred_paid_since_balance"
    ]
    schedule = [
        {
            "date": d,
            "pv": b["preferred_quarter_dividend"]
            / (1 + b["preferred_discount_rate"]) ** ((date.fromisoformat(d) - day).days / 365.25),
        }
        for d in b["dividend_dates"]
    ]
    dividends = sum(d["pv"] for d in schedule)
    pool = (
        enterprise
        + p["cash"]
        + elapsed
        - p["operating_cash"]
        + result["non_operating_asset_value"]
        - p["debt"]
        - p["other_claims"]
        - dividends
    )
    effective = p["shares"] * p["dilution"] + b["conversion_shares"]
    price = pool / effective
    result.update(
        enterprise_value=enterprise,
        equity_pool=pool,
        effective_shares=effective,
        equity_value=price * p["shares"] * p["dilution"],
        price=price,
        upside=price / p["market_price"] - 1 if pool > 0 else None,
        terminal_share=result["terminal_pv"] / enterprise if enterprise > 0 else None,
        research_transition={
            "quarter_fcff": s["quarter_fcff"],
            "estimated_elapsed_cash": elapsed,
            "remaining_fcff": remaining,
            "remaining_pv": remaining_pv,
            "dividend_pv": dividends,
            "dividend_schedule": schedule,
            "conversion_shares": b["conversion_shares"],
        },
    )
    return result

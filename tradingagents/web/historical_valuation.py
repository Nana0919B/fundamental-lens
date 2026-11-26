"""Point-in-time research snapshots consumed by the regular research valuation API.

This module adapts evidence and share units only. It contains no DCF formula.
"""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

from tradingagents.web import valuation

POLICY_VERSION = valuation.ASSUMPTION_POLICY


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def research_job(recent, day, native_shares, native_price, ticker):
    if not recent or len(recent) < recent[-1].get("minimum_history", 4):
        raise ValueError("需要四份原始年报")
    for snapshot in recent:
        if snapshot["filed"] >= day or snapshot["end"] >= day:
            raise ValueError("历史快照包含尚未公开的财报")
        for fact in snapshot["evidence"].values():
            if fact["filed"] >= day or fact["end"] > day:
                raise ValueError("原始证据晚于历史估值日")
    latest = recent[-1]

    # Inputs are assembled from the same originals used by the source ledger.
    def amount(s, key):
        if key == "da":
            return (
                s["evidence"]["da"]["val"]
                if "da" in s["evidence"]
                else (
                    sum(s["evidence"][k]["val"] for k in ("depreciation", "amortization"))
                    if all(k in s["evidence"] for k in ("depreciation", "amortization"))
                    else None
                )
            )
        return s["evidence"].get(key, {}).get("val")

    ordered = list(reversed(recent))
    statements = {
        "annual": {
            "income": {
                "periods": [s["end"] for s in ordered],
                "rows": [
                    {"name": "Total Revenue", "values": [s["revenue"] for s in ordered]},
                    {"name": "Operating Income", "values": [amount(s, "ebit") for s in ordered]},
                ],
            },
            "cashflow": {
                "periods": [s["end"] for s in ordered],
                "rows": [
                    {
                        "name": "Depreciation And Amortization",
                        "values": [amount(s, "da") for s in ordered],
                    },
                    {
                        "name": "Capital Expenditure",
                        "values": [
                            -amount(s, "capex") if amount(s, "capex") is not None else None
                            for s in ordered
                        ],
                    },
                ],
            },
        }
    }
    cash = sum(
        latest["evidence"].get(k, {}).get("val", 0)
        for k in ("cash", "short_investments", "long_investments")
    )
    company = {
        "symbol": ticker,
        "source": latest.get("source_name", "SEC original 10-K + Yahoo historical prices"),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "period_note": f"历史估值日 {day}；原始年报披露次日起可用；未获取今日基本面或新闻。",
        "profile": {
            "longName": ticker + " · 历史估值研究",
            "currency": latest.get("currency", "USD"),
            "financialCurrency": latest.get("currency", "USD"),
            "totalCash": cash,
            "totalDebt": latest["debt"],
            "sharesOutstanding": native_shares,
            "currentPrice": native_price,
            "valuationOtherClaims": latest["evidence"].get("other_claims", {}).get("val"),
        },
        "statements": statements,
    }
    snapshot = {"schema_version": 2, "as_of_date": day, "company": company, "peers": []}
    ledger = [
        {"end": s["end"], "filed": s["filed"], "accn": s["accn"], "facts": deepcopy(s["evidence"])}
        for s in recent
    ]
    context = {
        "as_of_date": day,
        "policy_version": POLICY_VERSION,
        "source_ledger": ledger,
        "source_warnings": latest["warnings"],
        "basis": "historical_public_facts",
        "assumption_status": "retrospective_research_defaults",
        "note": "今天定义的研究默认假设应用于历史公开数据；不是当年已归档的 AI 研究或预测。",
    }
    # Retrieval timestamp is metadata, not part of the financial snapshot hash.
    canonical = deepcopy(snapshot)
    canonical["company"].pop("retrieved_at")
    return {
        "ticker": ticker,
        "date": day,
        "status": "completed",
        "peers": [],
        "reports": {},
        "research_snapshot": snapshot,
        "snapshot_digest": digest({"snapshot": canonical, "context": context}),
        "historical_context": context,
    }


def calculate_history(recent, day, native_shares, native_price, ticker, template=None):
    job = research_job(recent, day, native_shares, native_price, ticker)
    if template:
        job["historical_context"]["forecast_template"] = deepcopy(template)
    inputs = valuation.prepare(job)["inputs"]
    result = valuation.calculate(job, inputs)
    job["valuation"] = result
    return job, result

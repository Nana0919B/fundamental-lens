"""One-factor diagnostic; does not change production rules or select parameters."""

import json
from copy import deepcopy
from pathlib import Path

from tradingagents.web.valuation_backtest import simulate

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "results/local-deployment/data/web"
RUN = "6764899b76544bb999625dccd0a5181d"


def diagnose():
    original = json.loads((STORE / "backtests" / f"{RUN}.json").read_text())
    output = {"source_run": RUN, "note": "事后单因素诊断；不是参数优化或样本外验证", "results": []}
    for result in original["result"]["results"]:
        p = original["parameters"]
        prices = json.loads(
            sorted((STORE / "backtest-cache").glob(f"{result['ticker']}-prices-*.json"))[
                -1
            ].read_text()
        )
        rows = [r for r in prices if p["start"] <= r["date"] <= p["end"]]
        ds = result["decisions"]
        valid = [d for d in ds if "error" not in d["valuation"]]
        summary = {
            "ticker": result["ticker"],
            "months": len(valid),
            "threshold_counts": {},
            "variants": [],
        }
        for scenario, margin in [
            ("bear", 0.2),
            ("bear", 0),
            ("base", 0.2),
            ("base", 0),
            ("bull", 0),
        ]:
            matches = [
                d
                for d in valid
                if d["close"] <= d["valuation"]["scenarios"][scenario]["price"] * (1 - margin)
            ]
            summary["threshold_counts"][f"{scenario}_{margin}"] = {
                "months": len(matches),
                "first": matches[0]["date"] if matches else None,
            }
        closest = min(
            valid, key=lambda d: d["close"] / d["valuation"]["scenarios"]["bear"]["price"]
        )
        summary["closest_bear"] = {
            "date": closest["date"],
            "price_to_value": closest["close"] / closest["valuation"]["scenarios"]["bear"]["price"],
        }
        summary["growth_capped_months"] = sum(d["valuation"]["revenue_cagr"] > 0.12 for d in valid)
        summary["median_to_latest_fcff"] = {
            d["date"]: {
                "cagr": d["valuation"]["revenue_cagr"],
                "median": d["valuation"]["normalized_fcff"],
                "latest": d["valuation"]["fcff_history"][-1],
            }
            for d in valid
            if d["date"] in ("2020-03-31", "2024-03-28", "2025-03-31")
        }
        for label, cash, cap, buy_scenario, margin in [
            ("original", "median", 0.12, "bear", 0.2),
            ("remove_safety_margin", "median", 0.12, "bear", 0),
            ("base_with_20pct_margin", "median", 0.12, "base", 0.2),
            ("growth_cap_30_only", "median", 0.30, "bear", 0.2),
            ("latest_fcff_only", "latest", 0.12, "bear", 0.2),
            ("latest_fcff_and_cap_30", "latest", 0.30, "bear", 0.2),
        ]:
            values = {}
            eligible = []
            for decision in ds:
                v = deepcopy(decision["valuation"])
                if "error" not in v:
                    growth = min(cap, max(-0.02, v["revenue_cagr"]))
                    cf = v["normalized_fcff"] if cash == "median" else v["fcff_history"][-1]
                    if cf <= 0:
                        v = {"error": "Latest FCFF nonpositive"}
                    else:
                        for name, offset in [("bear", -0.04), ("base", 0), ("bull", 0.04)]:
                            s = v["scenarios"][name]
                            w = s["wacc"]
                            g = s["terminal_growth"]
                            forecast = [cf * (1 + growth + offset) ** i for i in range(1, 6)]
                            ev = (
                                sum(f / (1 + w) ** i for i, f in enumerate(forecast, 1))
                                + forecast[-1] * (1 + g) / (w - g) / (1 + w) ** 5
                            )
                            s["price"] = (
                                max(0, ev + v["cash_bridge"] - v["debt"]) / v["normalized_shares"]
                            )
                        if decision["close"] <= v["scenarios"][buy_scenario]["price"] * (
                            1 - margin
                        ):
                            eligible.append(decision["date"])
                        v["scenarios"]["bear"]["price"] = v["scenarios"][buy_scenario]["price"]
                values[decision["date"]] = v
            sim = simulate(rows, values, {**p, "safety_margin": margin})
            if label == "original":
                assert (
                    abs(sim["strategy"]["total_return"] - result["strategy"]["total_return"])
                    < 1e-10
                )
                assert sim["trades"] == [
                    {k: v for k, v in t.items() if k != "native_price"} for t in result["trades"]
                ]
            summary["variants"].append(
                {
                    "name": label,
                    "eligible_months": len(eligible),
                    "first_eligible": eligible[0] if eligible else None,
                    "trades": len(sim["trades"]),
                    "first_buy": next(
                        (t["date"] for t in sim["trades"] if t["action"] == "BUY"), None
                    ),
                    "total_return": sim["strategy"]["total_return"],
                }
            )
        output["results"].append(summary)
    return output


if __name__ == "__main__":
    result = diagnose()
    path = ROOT / "results/local-deployment/verification/backtest-diagnosis.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))

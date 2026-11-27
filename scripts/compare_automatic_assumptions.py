"""Diagnostic attribution, never saved as a valuation or used to tune forecasts.

Compare frozen industry policy against automatic research on identical verified
financial facts. Shapley contributions average every replacement order, including
the explicit horizon change. Missing historical equity claims are never set to zero.
"""

import argparse
import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tradingagents.web import valuation as v, valuation_policy as policy  # noqa: E402

GROUPS = ("horizon", "growth", "margins", "reinvestment", "tax", "discount_terminal", "equity_policy")


def compare(job):
    record = job["automatic_valuation"]
    if record["status"] != "completed":
        raise ValueError("Automatic valuation must complete first")
    new = v.validate(record["prepared"]["inputs"])
    company = job["research_snapshot"]["company"]
    old = policy.build(company, job["ticker"])
    if old is None:
        raise ValueError("No historical industry policy for this company")
    n = new["years"]
    if n < 5 or new.get("research_bridge"):
        raise ValueError("Unsupported attribution horizon or company bridge")
    outputs = {}
    for name in v.SCENARIOS:
        legacy, modern = old["scenarios"][name], new["scenarios"][name]

        def result(mask, legacy=legacy, modern=modern):
            chosen = {key for i, key in enumerate(GROUPS) if mask & (1 << i)}
            p = deepcopy(new)
            p["years"] = n if "horizon" in chosen else 5
            s = deepcopy(legacy)
            for key in ("growth", "margins"):
                path = modern[key] if key in chosen else legacy[key] + [legacy[key][-1]] * (n - 5)
                s[key] = path[:p["years"]]
            if "reinvestment" in chosen:
                for key in ("da_ratio", "capex_ratio", "nwc_ratio"):
                    s[key] = modern[key]
                for key in ("da_ratios", "capex_ratios"):
                    if key in modern:
                        s[key] = modern[key][:p["years"]]
            if "tax" in chosen:
                s["tax_rate"] = modern["tax_rate"]
            if "discount_terminal" in chosen:
                for key in ("wacc", "terminal_growth", "terminal_roic"):
                    s[key] = modern[key]
            if "equity_policy" in chosen:
                s["asset_haircut"] = modern["asset_haircut"]
            else:
                p["operating_cash"] = min(p["cash"], p["revenue"] * old["operating_cash_ratio"])
            return v.project(p, s)

        count = len(GROUPS)
        values = {mask: result(mask)["price"] for mask in range(1 << count)}
        contributions = {}
        for i, key in enumerate(GROUPS):
            total = 0
            for mask in range(1 << count):
                if mask & (1 << i):
                    continue
                size = mask.bit_count()
                weight = math.factorial(size) * math.factorial(count-size-1) / math.factorial(count)
                total += weight * (values[mask | (1 << i)] - values[mask])
            contributions[key] = total
        actual = job["valuation"]["scenarios"][name]
        assert math.isclose(values[(1 << count)-1], actual["price"], rel_tol=1e-10)
        assert math.isclose(values[0] + sum(contributions.values()), actual["price"], rel_tol=1e-10)
        shares = new["shares"] * new["dilution"]
        # Operating EV per existing share plus all equity claims. For preferred
        # structures the residual line also includes conversion allocation.
        components = {
            "operating_ev_per_share": actual["enterprise_value"] / shares,
            "cash_per_share": new["cash"] / shares,
            "operating_cash_per_share": -new["operating_cash"] / shares,
            "debt_per_share": -new["debt"] / shares,
            "non_operating_assets_per_share": actual["non_operating_asset_value"] / shares,
            "other_claims_per_share": -new["other_claims"] / shares,
        }
        components["capital_allocation_residual"] = actual["price"] - sum(components.values())
        outputs[name] = {
            "industry_on_verified_facts": values[0],
            "automatic": actual["price"],
            "shapley_per_share": contributions,
            "equity_bridge_per_share": components,
            "industry_assumptions": legacy,
            "automatic_assumptions": modern,
        }
    return {
        "ticker": job["ticker"], "job_id": job["id"], "date": job["date"],
        "automatic_run_id": record["run_id"], "policy_audit": old["audit"],
        "method": "Exact Shapley over all 128 combinations; fixed verified financial facts",
        "limitations": [
            "Counterfactual industry-policy values, not previously saved valuations",
            "Industry 5-year paths hold their final assumption if the horizon is extended",
            "Interim hybrids isolate numerical effects, not recommended coherent forecasts",
            "Missing old equity claims are not treated as zero; verified facts fixed in both models",
            "Price never used to choose or optimize assumptions",
        ],
        "facts": {key: new.get(key) for key in (
            "revenue", "cash", "operating_cash", "debt", "shares", "dilution",
            "non_operating_assets", "other_claims", "balance_date", "market_price", "market_price_date",
        )},
        "scenarios": outputs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with urlopen("http://127.0.0.1:8766/api/jobs/" + args.job_id, timeout=30) as response:
        job = json.load(response)
    comparison = compare(job)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2))
    print(json.dumps({key: value["automatic"] for key, value in comparison["scenarios"].items()}))

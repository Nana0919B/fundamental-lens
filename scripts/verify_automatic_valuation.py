"""Replay a current automatic valuation from its archived evidence, without legacy plans.

Usage: .venv/bin/python scripts/verify_automatic_valuation.py JOB_ID
"""

import argparse
import calendar
import hashlib
import json
import math
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingagents.web import automatic_valuation as av, company_research, valuation as v  # noqa: E402


def check_close(a, b):
    if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7):
        raise AssertionError(f"Independent recomputation differs: {a} != {b}")


def independent_equity(p, scenario):
    """Separately implement ordinary FCFF + the mandatory-preferred equity allocation."""
    s = p["scenarios"][scenario]
    revenue, pv = p["revenue"], 0.0
    for i in range(p["years"]):
        before = revenue
        revenue *= 1 + s["growth"][i]
        ebit = revenue * s["margins"][i]
        fcff = (
            ebit
            - max(ebit, 0) * s["tax_rate"]
            + revenue * s["da_ratios"][i]
            - revenue * s["capex_ratios"][i]
            - (revenue - before) * s["nwc_ratio"]
        )
        pv += fcff / (1 + s["wacc"]) ** (i + 1)
    terminal = (
        revenue
        * (1 + s["terminal_growth"])
        * s["margins"][-1]
        * (1 - s["tax_rate"])
        * (1 - s["terminal_growth"] / s["terminal_roic"])
    )
    enterprise = pv + terminal / (s["wacc"] - s["terminal_growth"]) / (1 + s["wacc"]) ** p["years"]
    cash_claims, conversion = 0.0, 0.0
    if p.get("capital_structure"):
        day, balance = (
            date.fromisoformat(p["valuation_date"]),
            date.fromisoformat(p["balance_date"]),
        )

        def value(t):
            return t["amount"] * t.get("scale", 1)

        for group in p["capital_structure"]["terms"]["groups"]:
            quantity = value(group["depositary_shares"]) / value(group["receipts_per_preferred"])
            conversion += (
                quantity
                * sum(value(row["conversion_max"]) for row in group["series"])
                / len(group["series"])
            )
            end = date.fromisoformat(group["conversion_date"]["date"])
            first = date.fromisoformat(group["first_dividend_date"]["date"])
            schedule = group["payment_schedule"]
            amount = (
                quantity
                * value(group["liquidation_per_preferred"])
                * value(group["coupon_percent"])
                / 100
                / len(schedule["months"])
            )
            for year in range(balance.year, end.year + 1):
                for month in schedule["months"]:
                    payment = date(
                        year, month, min(schedule["day"], calendar.monthrange(year, month)[1])
                    )
                    if max(first, balance) < payment <= end:
                        cash_claims += (
                            amount
                            if payment <= day
                            else amount
                            / (1 + group["dividend_discount_rate"])
                            ** ((payment - day).days / 365.25)
                        )
    pool = (
        enterprise
        + p["cash"]
        - p["operating_cash"]
        + p["non_operating_assets"] * (1 - s["asset_haircut"])
        - p["debt"]
        - p["other_claims"]
        - cash_claims
    )
    return {
        "price": pool / (p["shares"] * p["dilution"] + conversion),
        "enterprise_value": enterprise,
        "conversion_shares": conversion,
        "dividend_claim": cash_claims,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--store", type=Path, default=ROOT / "results/local-deployment/data/web")
    args = parser.parse_args()
    with urlopen(args.base_url + "/api/jobs/" + args.job_id, timeout=30) as response:
        job = json.load(response)
    record = job.get("automatic_valuation", {})
    if record.get("status") != "completed" or record.get("version") not in ("research-auto-1.0", "research-auto-1.1", "research-auto-1.2", "research-auto-1.3", "research-auto-1.3.1", "research-auto-1.3.2", "research-auto-1.3.3", "research-auto-1.3.4"):
        raise ValueError("Only completed compatible automatic plans can be replayed")
    av.VERSION = record["version"]  # preserve archived metadata during replay
    folder = args.store / "valuation-research" / args.job_id / record["run_id"]
    docs = [json.loads(p.read_text()) for p in sorted(folder.glob("S*.json"))]
    for doc in docs:
        raw = (folder / (doc["id"] + ".html")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == doc["sha256"], "Source file checksum mismatch"
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup.select(
            'script, style, [style*="display:none"], [style*="display: none"], ix\\:hidden'
        ):
            tag.decompose()
        assert av.normalize(soup.get_text(" ", strip=True)) == doc["text"], (
            "Source text differs from original HTML"
        )
    quote = json.loads((folder / "quote.json").read_text())
    prepared = av.compile_plan(job, record["plan"], docs, quote)
    assert prepared == record["prepared"], "Compiled source plan differs"
    with patch.object(
        company_research, "build", side_effect=AssertionError("Legacy manual plan accessed")
    ):
        replay = v.calculate(job, prepared["inputs"])
    saved = job["valuation"]
    for key in ("input_digest", "scenarios", "sensitivity", "reverse"):
        assert replay[key] == saved[key], "Saved result differs: " + key
    checks = {}
    for name, result in replay["scenarios"].items():
        independent = independent_equity(replay["inputs"], name)
        for key in ("price", "enterprise_value"):
            check_close(independent[key], result[key])
        checks[name] = independent
    for i, row in enumerate(replay["sensitivity"]["prices"]):
        for k, price in enumerate(row):
            if price is None:
                continue
            trial = deepcopy(replay["inputs"])
            trial["scenarios"]["base"].update(
                wacc=replay["sensitivity"]["waccs"][i],
                terminal_growth=replay["sensitivity"]["growths"][k],
            )
            check_close(independent_equity(trial, "base")["price"], price)
    independent_reverse = False
    reverse_bound = None
    if replay["reverse"]["status"] == "solved":
        trial = deepcopy(replay["inputs"])
        trial["scenarios"]["base"]["margins"] = [
            m + replay["reverse"]["margin_shift"] for m in trial["scenarios"]["base"]["margins"]
        ]
        check_close(independent_equity(trial, "base")["price"], replay["reverse"]["target_price"])
        independent_reverse = True
    elif replay["reverse"]["status"] == "no_solution":
        trial = deepcopy(replay["inputs"])
        margins = trial["scenarios"]["base"]["margins"]
        shift = 0.6 - max(margins)
        trial["scenarios"]["base"]["margins"] = [m + shift for m in margins]
        reverse_bound = independent_equity(trial, "base")["price"]
        # With positive revenue and terminal ROIC > growth, equity increases
        # monotonically with a parallel margin shift. Below-target at the upper
        # bound independently proves no root within the allowed margin range.
        independent_reverse = reverse_bound < trial["market_price"]
    audit = {
        "job_id": args.job_id,
        "run_id": record["run_id"],
        "version": av.VERSION,
        "input_digest": replay["input_digest"],
        "manual_plan_accessed": False,
        "source_replay": True,
        "sensitivity_replay": True,
        "reverse_replay": True,
        "independent_recomputation": checks,
        "independent_sensitivity": True,
        "independent_reverse": independent_reverse,
        "independent_reverse_upper_price": reverse_bound,
    }
    av.save(folder / "replay-audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

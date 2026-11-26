"""Verify every archived valuation against the regular research calculation API."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from tradingagents.web.valuation import calculate


def verify(path):
    record = json.loads(path.read_text())
    report = {"run_id": record["id"], "status": record["status"], "checked": 0, "tickers": []}
    assert record["status"] == "completed"
    for result in record["result"]["results"]:
        values = [d["valuation"] for d in result["decisions"]] + [result["inspection"]]
        for value in values:
            assert "error" not in value, value
            job = value["research_job"]
            expected = job["valuation"]
            actual = calculate(job, expected["inputs"])
            # Removing the PIT UI context must not change any valuation formula.
            ordinary = deepcopy(job)
            ordinary.pop("historical_context")
            plain = calculate(ordinary, expected["inputs"])
            for key in ("scenarios", "sensitivity", "reverse", "input_digest"):
                assert actual[key] == expected[key], (result["ticker"], value["date"], key)
                assert plain[key] == expected[key], (result["ticker"], value["date"], key)
            for scenario in expected["scenarios"]:
                assert (
                    value["scenarios"][scenario]["native_price"]
                    == expected["scenarios"][scenario]["price"]
                )
                assert (
                    value["scenarios"][scenario]["price"]
                    == expected["scenarios"][scenario]["price"] / value["native_factor"]
                )
            for filing in job["historical_context"]["source_ledger"]:
                assert filing["filed"] < value["date"]
                assert all(
                    f["filed"] < value["date"] and f["end"] <= value["date"]
                    for f in filing["facts"].values()
                )
            report["checked"] += 1
        report["tickers"].append(
            {
                "ticker": result["ticker"],
                "monthly": result["valuation_coverage"],
                "trades": len(result["trades"]),
                "strategy": result["strategy"],
                "inspection_prices": {
                    k: v["native_price"] for k, v in result["inspection"]["scenarios"].items()
                },
            }
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.record)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text)
    print(text)

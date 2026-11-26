"""Reproduce the dated BA research valuation against the running local service.

Run from the repository root: .venv/bin/python scripts/revalue_ba.py
Inputs are frozen public disclosures plus separately labelled research assumptions.
"""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--job", default="0795bd20e1974c13bc49ebbae225cf71")
    args = parser.parse_args()
    if not args.job.isalnum():
        raise ValueError("Invalid research id")
    url = f"http://127.0.0.1:{args.port}/api/jobs/{args.job}"
    with urlopen(url, timeout=20) as response:
        job = json.load(response)
    if job.get("ticker") != "BA":
        raise ValueError("The selected research must be BA")
    inputs = json.loads((ROOT / "research/valuations/BA-2026-11-26-inputs.json").read_text())
    request = Request(
        url + "/valuation",
        json.dumps(inputs).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    with urlopen(url, timeout=20) as response:
        after = json.load(response)
    assert job["research_snapshot"] == after["research_snapshot"]
    assert job["snapshot_digest"] == after["snapshot_digest"] == result["snapshot_digest"]
    assert result["sensitivity"]["prices"][2][2] == result["scenarios"]["base"]["price"]
    assert abs(result["reverse"]["reproduced_price"] - inputs["market_price"]) < 1e-7
    for s in result["scenarios"].values():
        assert abs(s["equity_value"] / inputs["shares"] - s["price"]) < 1e-8
        assert (
            abs(s["equity_value"] + s["converted_preferred_equity_value"] - s["equity_pool"])
            < 0.001
        )
    out = ROOT / "results/local-deployment/verification/ba-revaluation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "valuation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    checks = {
        "valuation_id": result["id"],
        "snapshot_unchanged": True,
        "source_input_sha256": hashlib.sha256(
            json.dumps(inputs, sort_keys=True).encode()
        ).hexdigest(),
        "reference_price": inputs["market_price"],
        "valuation_date": inputs["valuation_date"],
        "matrix_center_matches": True,
        "reverse_price_matches": True,
        "scenarios": {
            k: {
                field: v[field]
                for field in [
                    "price",
                    "equity_value",
                    "enterprise_value",
                    "upside",
                    "effective_shares",
                    "terminal_share",
                    "conversion_price_range",
                ]
            }
            for k, v in result["scenarios"].items()
        },
    }
    (out / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

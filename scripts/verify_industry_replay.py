"""Audit the frozen industry policy and both completed API runs (no tuning)."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path

from verify_unified_backtest import verify

from tradingagents.web import valuation_backtest as bt, valuation_policy as policy
from tradingagents.web.backtest_hk import MANIFEST


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints():
    return {
        "policy_sha256": sha(policy.__file__),
        "reviewed_inputs_sha256": sha(MANIFEST),
        "execution_rule_sha256": hashlib.sha256(
            inspect.getsource(bt.simulate).encode()
        ).hexdigest(),
    }


def audit(freeze, records):
    frozen = json.loads(freeze.read_text())
    assert frozen["fingerprints"] == fingerprints(), "Policy changed after freeze"
    reports = []
    for path in records:
        row = json.loads(path.read_text())
        p = row["parameters"]
        assert p["safety_margin"] == 0.2
        assert not p.get("research_template")
        report = verify(path)
        for stock in row["result"]["results"]:
            for v in [d["valuation"] for d in stock["decisions"]] + [stock["inspection"]]:
                j = v["research_job"]
                audit_data = j["valuation"]["policy_audit"]
                assert audit_data["version"] == policy.VERSION
                assert (
                    audit_data["implementation_sha256"] == frozen["fingerprints"]["policy_sha256"]
                )
                for filing in j["historical_context"]["source_ledger"]:
                    for fact in filing["facts"].values():
                        assert fact.get("fx_date", "0001-01-01") < v["date"]
            for trade in stock["trades"]:
                assert trade["date"] > trade["signal_date"]
        report["parameters"] = p
        report["excluded"] = row["result"].get("excluded", {})
        report["benchmark"] = {s["ticker"]: s["benchmark"] for s in row["result"]["results"]}
        reports.append(report)
    return {
        "fingerprints": fingerprints(),
        "runs": reports,
        "total_parity_checks": sum(x["checked"] for x in reports),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("freeze", type=Path)
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.freeze, args.records)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "Verified",
        result["total_parity_checks"],
        "shared-engine snapshots; policy and trading rules unchanged.",
    )

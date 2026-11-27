"""Version lineage and immutable macro inputs for real reanalysis, not an appendix."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tradingagents.agents.macro_context import validate_macro_context
from tradingagents.dataflows.research_store import ResearchStore


def data():
    snapshot = {
        "id": "financial-one",
        "as_of_date": "2026-11-13",
        "company": {"symbol": "MSFT", "profile": {}},
        "evidence": [],
    }
    macro = {
        "id": "macro-one",
        "status": "completed",
        "as_of_date": "2026-11-13",
        "retrieved_at": "2026-11-13T12:00:00+08:00",
        "reports": ["FRED observation"],
        "source_mode": "fred_public_latest",
    }
    return snapshot, macro


def test_report_versions_do_not_replace_financial_data_or_original_reports(tmp_path):
    store = ResearchStore(tmp_path / "db.sqlite")
    snapshot, macro = data()
    original = {"news_report": "old news", "final_trade_decision": "old decision"}
    digest = store.archive_snapshot(snapshot)
    store.archive_reports(snapshot, original)
    new = {"news_report": "new FRED-based news", "final_trade_decision": "new decision"}
    store.archive_report_version(
        "run-two", snapshot, macro, new, "financial-one", "2026-11-13T13:00:00+08:00"
    )
    assert store.archive_snapshot(snapshot) == digest
    with store.connect() as db:
        assert json.loads(db.execute("SELECT payload FROM reports").fetchone()[0]) == original
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        row = db.execute("SELECT * FROM report_versions").fetchone()
        assert row["snapshot_id"] == "financial-one"
        assert json.loads(row["macro_payload"]) == macro
    assert {v["id"] for v in store.history("MSFT")} == {"financial-one", "run-two"}
    with pytest.raises(ValueError):
        store.archive_report_version(
            "run-two", snapshot, macro, original, "financial-one", "2026-11-13T13:00:00+08:00"
        )


def test_starting_reanalysis_clones_inputs_and_never_fetches_financials(monkeypatch, tmp_path):
    from tradingagents.web import server

    snapshot, macro = data()
    seeds = {
        key: "saved " + key for key in ("fundamentals_report", "operating_report", "metrics_report")
    }
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    parent = {
        "id": "parent",
        "ticker": "MSFT",
        "status": "completed",
        "research_snapshot": snapshot,
        "snapshot_digest": digest,
        "reports": {**seeds, "news_report": "old news"},
        "macro_update": macro,
    }
    original = deepcopy(parent)
    monkeypatch.setattr(server, "JOBS", {"parent": parent})
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "today", lambda: "2026-11-13")
    calls = []
    monkeypatch.setattr(server, "POOL", SimpleNamespace(submit=lambda *args: calls.append(args[2])))
    job = server.create_macro_reanalysis("parent")
    assert job["id"] != "parent" and calls == [job["id"]]
    assert job["research_snapshot"] == snapshot and job["macro_context"] == macro
    assert job["seed_reports"] == seeds and "news_report" not in job["seed_reports"]
    assert server.JOBS["parent"] == original
    parent["macro_update"]["reports"][0] = "mutated"
    assert job["macro_context"]["reports"][0] == "FRED observation"


def test_macro_context_rejects_unfinished_wrong_date_and_empty_values():
    _, macro = data()
    for update in (
        {"status": "queued"},
        {"as_of_date": "2026-11-12"},
        {"retrieved_at": "2026-11-14T00:00:00+08:00"},
        {"reports": []},
    ):
        with pytest.raises(ValueError):
            validate_macro_context({**macro, **update}, "2026-11-13")


def test_failed_hong_kong_run_can_finish_after_midnight_without_replacing_inputs(monkeypatch, tmp_path):
    from tradingagents.web import server

    snapshot, _ = data()
    snapshot["company"]["symbol"] = "0700.HK"
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    reports = {key: "original " + key for key in (
        "fundamentals_report", "operating_report", "metrics_report", "news_report"
    )}
    job = {
        "id": "hk", "ticker": "0700.HK", "date": "2026-11-13", "status": "failed",
        "error": "AnthropicConnectionError", "research_snapshot": snapshot,
        "snapshot_digest": digest, "reports": reports,
    }
    monkeypatch.setattr(server, "JOBS", {"hk": job})
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "today", lambda: "2026-11-14")
    calls = []
    monkeypatch.setattr(server, "POOL", SimpleNamespace(submit=lambda *args: calls.append(args[2])))
    original = deepcopy(job)
    result = server.retry_job("hk")
    assert result["status"] == "queued" and result["error"] is None
    assert calls == ["hk"] and result["seed_reports"] == reports
    for key in ("date", "reports", "research_snapshot", "snapshot_digest"):
        assert result[key] == original[key]
    result["reports"]["news_report"] = "changed"
    assert result["seed_reports"]["news_report"] == "original news_report"


@pytest.mark.parametrize("damage", ["incomplete", "digest"])
def test_old_failed_runs_require_complete_untampered_inputs(monkeypatch, tmp_path, damage):
    from tradingagents.web import server

    snapshot, _ = data()
    reports = dict.fromkeys(("fundamentals_report", "operating_report", "metrics_report", "news_report"), "saved")
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    if damage == "incomplete":
        del reports["news_report"]
    else:
        digest = "invalid"
    job = {
        "id": "old", "ticker": "MSFT", "date": "2026-11-13", "status": "failed",
        "research_snapshot": snapshot, "snapshot_digest": digest, "reports": reports,
    }
    monkeypatch.setattr(server, "JOBS", {"old": job})
    monkeypatch.setattr(server, "today", lambda: "2026-11-14")
    with pytest.raises(ValueError):
        server.retry_job("old")
    assert job["status"] == "failed"

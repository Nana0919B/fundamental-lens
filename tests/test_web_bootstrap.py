"""A fresh checkout can browse the shared archive without credentials or network calls."""

import hashlib
import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from tradingagents.web.bootstrap import SEED_DIR, initialize_shared_data


def test_bundled_archive_is_complete_and_contains_no_aapl():
    manifest = json.loads((SEED_DIR / "manifest.json").read_text())
    assert manifest["tickers"] == ["0700.HK", "INTC", "MSFT"]
    with sqlite3.connect(f"file:{SEED_DIR / 'research.sqlite3'}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table, expected in manifest["table_counts"].items():
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == expected
        jobs = list((SEED_DIR / "jobs").glob("*.json"))
        assert len(jobs) == manifest["job_count"] == 4
        for path in jobs:
            job = json.loads(path.read_text())
            assert job["ticker"] != "AAPL" and job["status"] == "completed"
            raw = json.dumps(job["research_snapshot"], ensure_ascii=False, sort_keys=True, allow_nan=False)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            assert digest == job["snapshot_digest"]
            assert db.execute("SELECT digest FROM snapshots WHERE id=?", (
                job["research_snapshot"]["id"],
            )).fetchone()[0] == digest
            if job.get("parent_job_id"):
                row = db.execute("SELECT report_payload FROM report_versions WHERE id=?", (job["id"],)).fetchone()
            else:
                row = db.execute("SELECT payload FROM reports WHERE snapshot_id=?", (
                    job["research_snapshot"]["id"],
                )).fetchone()
            assert json.loads(row[0]) == job["reports"]
        for line in db.iterdump():
            assert "AAPL" not in line and "/Users/" not in line


@pytest.mark.parametrize("precreate", [False, True])
def test_first_start_loads_history_and_exports_without_network(monkeypatch, tmp_path, precreate):
    from tradingagents.web import server

    store = tmp_path / "web"
    if precreate:
        store.mkdir()
    assert initialize_shared_data(store)
    monkeypatch.setattr(server, "STORE", store)
    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server, "build_default_config", lambda: {"results_dir": str(tmp_path)})
    monkeypatch.setattr(server, "company_data", lambda *a: pytest.fail("Archive viewing must stay offline"))
    server.load_history()
    assert len(server.JOBS) == 4
    assert {job["ticker"] for job in server.JOBS.values()} == {"MSFT", "0700.HK", "INTC"}
    for job_id in server.JOBS:
        handler = object.__new__(server.Handler)
        handler.headers = {"Host": "127.0.0.1:8765"}
        handler.server = SimpleNamespace(server_port=8765)
        responses = []
        handler.send = lambda data, status=200, responses=responses, **kwargs: responses.append((status, data))
        handler.path = f"/api/jobs/{job_id}"
        handler.do_GET()
        assert responses[-1][0] == 200 and responses[-1][1]["report_html"]["final_trade_decision"]
        handler.path += "/export"
        handler.do_GET()
        status, html = responses[-1]
        assert status == 200 and b"FundAnalyst" in html
        assert not (store / "exports").exists()  # Restored data can export without old generated files.
    assert not initialize_shared_data(store)


@pytest.mark.parametrize("filename", ["research.sqlite3", "existing.json", "cache/keep.txt"])
def test_existing_local_data_is_never_overwritten(tmp_path, filename):
    store = tmp_path / "web"
    existing = store / filename
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"developer local data")
    assert not initialize_shared_data(store)
    assert existing.read_bytes() == b"developer local data"
    assert len(list(store.rglob("*"))) == (2 if "/" in filename else 1)


def test_corrupted_seed_leaves_no_partial_database_or_jobs(tmp_path):
    seed = tmp_path / "seed"
    shutil.copytree(SEED_DIR, seed)
    (seed / "research.sqlite3").write_bytes(b"truncated download")
    store = tmp_path / "web"
    with pytest.raises(ValueError, match="checksum mismatch"):
        initialize_shared_data(store, seed)
    assert not store.exists()

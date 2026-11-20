"""Refresh is bounded, optional, shared and skipped for frozen/historical runs."""

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows import trendradar_refresh as refresh

NOW = datetime(2026, 1, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
DAY = "2026-01-09"


def success(root, kind, day=DAY, stamp="11:50", status="success"):
    folder = root / kind
    folder.mkdir(parents=True, exist_ok=True)
    records, statuses, source = (("crawl_records", "crawl_source_status", "platform_id")
                                if kind == "news" else ("rss_crawl_records", "rss_crawl_status", "feed_id"))
    with sqlite3.connect(folder / f"{day}.db") as db:
        db.execute(f"CREATE TABLE IF NOT EXISTS {records}(id INTEGER,crawl_time TEXT)")
        db.execute(f"CREATE TABLE IF NOT EXISTS {statuses}(crawl_record_id INTEGER,{source} TEXT,status TEXT)")
        db.execute(f"INSERT INTO {records} VALUES (1,?)", (stamp,))
        db.execute(f"INSERT INTO {statuses} VALUES (1,'source',?)", (status,))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "trendradar/crawler").mkdir(parents=True)
    (project / "trendradar/crawler/fetcher.py").touch()
    config = {"trendradar_enabled": True, "trendradar_data_dir": str(project / "output")}
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW)
    return config


def fake_collector(monkeypatch, cfg, *, code=0):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--result-file") + 1]).write_text(json.dumps({
            "hotlist": {"items": 2, "failed": []}, "rss": {"items": 0, "failed": ["broken"]},
        }))
        success(Path(cfg["trendradar_data_dir"]), "news")
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(refresh.subprocess, "run", run)
    return calls


def test_fresh_source_data_skips_collection_even_without_checkout(cfg, monkeypatch):
    root = Path(cfg["trendradar_data_dir"])
    for kind in ("news", "rss"):
        success(root, kind)
    cfg["trendradar_project_dir"] = "/missing/optional-checkout"
    monkeypatch.setattr(refresh.subprocess, "run", lambda *a, **k: pytest.fail("unnecessary collection"))
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "reused"


def test_stale_data_refreshes_once_and_partial_success_is_reused(cfg, monkeypatch):
    root = Path(cfg["trendradar_data_dir"])
    success(root, "news", stamp="09:00")
    calls = fake_collector(monkeypatch, cfg)
    first = refresh.ensure_news_fresh(cfg, DAY)
    assert first["status"] == "refreshed"
    assert first["counts"]["rss"]["failed"] == ["broken"]
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "reused"
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert isinstance(command, list) and "shell" not in kwargs
    assert kwargs["timeout"] == 60
    assert command[command.index("--output-dir") + 1] == str(root)
    assert "trendradar_collect.py" in command[1]


@pytest.mark.parametrize("error,status", [
    (subprocess.TimeoutExpired("collector", 1), "timeout"),
    (FileNotFoundError("optional interpreter"), "failed"),
    (RuntimeError("unexpected optional runtime error"), "unavailable"),
])
def test_collection_errors_never_escape(cfg, monkeypatch, error, status):
    def fail(*a, **k):
        raise error

    monkeypatch.setattr(refresh.subprocess, "run", fail)
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == status
    if status != "unavailable":
        assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "cooldown"


def test_nonzero_exit_cools_down_then_retries(cfg, monkeypatch):
    calls = fake_collector(monkeypatch, cfg, code=1)
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "failed"
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "cooldown"
    assert len(calls) == 1
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW + timedelta(minutes=6))
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "failed"
    assert len(calls) == 2


def test_disabled_historical_and_missing_checkout_do_not_launch(cfg, monkeypatch):
    monkeypatch.setattr(refresh.subprocess, "run", lambda *a, **k: pytest.fail("should not launch"))
    assert refresh.ensure_news_fresh({}, DAY)["status"] == "disabled"
    assert refresh.ensure_news_fresh({**cfg, "trendradar_auto_refresh": False}, DAY)["status"] == "disabled"
    assert refresh.ensure_news_fresh(cfg, "2026-01-08")["status"] == "historical"
    cfg["trendradar_project_dir"] = "/missing/checkout"
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "unavailable"


def test_another_collector_does_not_block_analysis(cfg, monkeypatch):
    metadata = Path(cfg["trendradar_data_dir"]) / ".tradingagents-refresh"
    metadata.mkdir(parents=True)
    monkeypatch.setattr(refresh.subprocess, "run", lambda *a, **k: pytest.fail("duplicate collection"))
    with refresh.locked(metadata / "collector") as acquired:
        assert acquired
        assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "busy"


def test_refresh_precedes_freezing_and_build_failure_is_optional(cfg, monkeypatch):
    from tradingagents.dataflows import trendradar_adapter as adapter

    order = []
    monkeypatch.setattr(refresh, "ensure_news_fresh", lambda *a: order.append("refresh") or {"status": "timeout"})

    def build(*args):
        order.append("freeze")
        return {"items": []}

    monkeypatch.setattr(adapter, "build_news_context", build)
    context = refresh.prepare_news_context(cfg, "NVDA", DAY)
    assert order == ["refresh", "freeze"]
    assert context["refresh"]["status"] == "timeout"
    assert context["snapshot_id"] == adapter._digest(context)
    monkeypatch.setattr(adapter, "build_news_context", lambda *a: (_ for _ in ()).throw(OSError("unreadable")))
    assert refresh.prepare_news_context(cfg, "NVDA", DAY) is None


def test_previous_day_success_counts_across_midnight_but_failures_do_not(cfg, monkeypatch):
    root = Path(cfg["trendradar_data_dir"])
    monkeypatch.setattr(refresh, "utc_now", lambda: NOW.replace(hour=0, minute=5))
    for kind in ("news", "rss"):
        success(root, kind, day="2026-01-08", stamp="23:50")
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "reused"
    with sqlite3.connect(root / "rss/2026-01-08.db") as db:
        db.execute("UPDATE rss_crawl_status SET status='failed'")
    fake_collector(monkeypatch, cfg)
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "refreshed"


def test_corrupt_refresh_state_and_database_do_not_prevent_attempt(cfg, monkeypatch):
    root = Path(cfg["trendradar_data_dir"])
    meta = root / ".tradingagents-refresh"
    meta.mkdir(parents=True)
    (meta / "state.json").write_text("not json")
    (root / "news").mkdir()
    (root / f"news/{DAY}.db").write_bytes(b"not sqlite")

    def run(command, **kwargs):
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(refresh.subprocess, "run", run)
    assert refresh.ensure_news_fresh(cfg, DAY)["status"] == "failed"

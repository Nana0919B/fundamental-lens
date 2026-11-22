"""Built-in collection works without importing an external checkout or AI runtime."""

import importlib.abc
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from tradingagents.dataflows.trendradar_adapter import build_news_context, validate_news_context
from tradingagents.newsradar import collect_once
from tradingagents.newsradar.parsing import parse_feed, parse_hotlist, publication_time
from tradingagents.newsradar.sources import DEFAULT_SOURCES, load_sources
from tradingagents.newsradar.storage import save_source

NOW = datetime(2026, 1, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
RSS = b'''<rss version="2.0"><channel><title>News</title><item>
<title>NVIDIA earnings</title><link>https://example.com/rss</link><guid>rss-1</guid>
<pubDate>Fri, 09 Jan 2026 02:00:00 GMT</pubDate><description>&lt;p&gt;Actual summary&lt;/p&gt;</description>
</item></channel></rss>'''


class Session:
    def __init__(self, *, fail_feed=False, hotlist=None):
        self.headers = {}
        self.calls = []
        self.fail_feed = fail_feed
        self.hotlist = hotlist or {"status": "success", "items": [{"title": "英伟达发布芯片", "url": "https://example.com/chip"}]}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "params" in kwargs:
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.hotlist)
        if self.fail_feed:
            raise requests.HTTPError("unavailable")
        return SimpleNamespace(raise_for_status=lambda: None, content=RSS)


@pytest.fixture
def sources(tmp_path):
    config = {"platforms": {"api_url": "https://example.com/api", "sources": [
        {"id": "tech", "name": "Tech", "expected_domain": "example.com"},
    ]}, "rss": {"feeds": [{"id": "feed", "name": "Feed", "url": "https://example.com/feed"}]}}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(config))
    return path


def test_collection_writes_adapter_compatible_evidence_without_external_imports(tmp_path, sources, monkeypatch):
    class NoCheckout(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "trendradar" or fullname.startswith(("trendradar.", "litellm")):
                raise AssertionError("Unexpected external import: " + fullname)

    monkeypatch.setattr(sys, "meta_path", [NoCheckout(), *sys.meta_path])
    root = tmp_path / "output"
    session = Session()
    counts = collect_once(root, config_file=sources, session=session, clock=lambda: NOW)
    assert session.calls[0][1]["headers"]["User-Agent"].startswith("Mozilla/")
    assert session.headers["User-Agent"].startswith("TradingAgents-")
    assert counts == {"hotlist": {"items": 1, "failed": []}, "rss": {"items": 1, "failed": []}}
    cfg = {"trendradar_enabled": True, "trendradar_data_dir": str(root), "trendradar_lookback_days": 1}
    context = build_news_context(cfg, "NVDA", "2026-01-09", now=NOW + timedelta(seconds=1))
    assert len(context["items"]) == 2
    assert context["coverage"]["status"] == "partial"
    assert not context["coverage"]["sources_failed"]
    assert {item["source_type"] for item in context["items"]} == {"hotlist", "rss"}
    rss = next(item for item in context["items"] if item["source_type"] == "rss")
    assert rss["published_at"] == "2026-01-09T02:00:00+00:00"
    assert rss["summary"] == "Actual summary"
    validate_news_context(context, "NVDA", "2026-01-09")


def test_broken_feed_preserves_hotlist_and_records_failed_status(tmp_path, sources):
    session = Session(fail_feed=True)
    counts = collect_once(tmp_path / "output", config_file=sources, session=session, clock=lambda: NOW)
    assert counts["hotlist"]["items"] == 1
    assert counts["rss"] == {"items": 0, "failed": ["feed"]}
    with sqlite3.connect(tmp_path / "output/rss/2026-01-09.db") as db:
        assert db.execute("SELECT status FROM rss_crawl_status").fetchone()[0] == "failed"
    assert len(session.calls) == 3  # hotlist + two RSS attempts


def test_history_dedup_offlist_return_and_failed_source_do_not_corrupt_ranks(tmp_path):
    source = {"id": "tech", "name": "Tech"}
    item = {"title": "NVIDIA first", "url": "https://example.com/one", "rank": 8}
    path = save_source(tmp_path, "hotlist", source, [item], NOW)
    save_source(tmp_path, "hotlist", source, [{**item, "title": "NVIDIA revised", "rank": 2}], NOW + timedelta(minutes=1))
    save_source(tmp_path, "hotlist", source, [], NOW + timedelta(minutes=2), succeeded=False)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM news_items").fetchone()[0] == 1
        assert db.execute("SELECT rank,crawl_count FROM news_items").fetchone() == (2, 2)
    save_source(tmp_path, "hotlist", source, [], NOW + timedelta(minutes=3))
    save_source(tmp_path, "hotlist", source, [item], NOW + timedelta(minutes=4))
    with sqlite3.connect(path) as db:
        assert [r[0] for r in db.execute("SELECT rank FROM rank_history ORDER BY crawl_time")] == [8, 2, 0, 8]
        assert db.execute("SELECT count(*) FROM news_items").fetchone()[0] == 1


def test_rss_guid_update_keeps_identity_when_url_changes(tmp_path):
    source = {"id": "feed", "name": "Feed", "url": "https://example.com/rss"}
    item = {"title": "NVIDIA", "url": "https://example.com/old", "guid": "stable"}
    path = save_source(tmp_path, "rss", source, [item], NOW)
    save_source(tmp_path, "rss", source, [{**item, "url": "https://example.com/new"}], NOW + timedelta(minutes=1))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*),url,crawl_count FROM rss_items").fetchone() == (1, "https://example.com/new", 2)


@pytest.mark.parametrize("url", ["https://example.com@evil.test/a", "http://example.com/a", "https://example.com.evil.test/a", "javascript:alert(1)"])
def test_hotlist_rejects_mismatched_links(url):
    with pytest.raises(ValueError, match="domain"):
        parse_hotlist({"status": "cache", "items": [{"title": "NVIDIA", "url": url}]}, "example.com")


def test_rss_atom_json_feed_and_timezone_normalization():
    assert publication_time("2026-01-09T10:00:00") == "2026-01-09T10:00:00+00:00"
    assert publication_time("2026-01-09T10:00:00+08:00") == "2026-01-09T02:00:00+00:00"
    assert publication_time("unknown") is None
    atom = '''<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry>
        <title>NVIDIA Atom</title><id>atom-1</id><link href="https://example.com/atom"/>
        <updated>2026-01-09T10:00:00+08:00</updated></entry></feed>'''
    assert parse_feed(atom)[0]["published_at"] == "2026-01-09T02:00:00+00:00"
    feed = {"version": "https://jsonfeed.org/version/1.1", "items": [
        {"id": "one", "url": "https://example.com/json", "title": "NVIDIA JSON", "date_published": "2026-01-09T10:00:00+08:00"},
    ]}
    assert parse_feed(b"\xef\xbb\xbf" + json.dumps(feed).encode())[0]["published_at"] == "2026-01-09T02:00:00+00:00"
    assert parse_feed(RSS)[0]["summary"] == "Actual summary"


def test_default_configuration_is_independent_and_overrides_replace_feeds(tmp_path):
    data = load_sources()
    data["rss"]["feeds"].clear()
    assert DEFAULT_SOURCES["rss"]["feeds"]
    file = tmp_path / "sources.json"
    file.write_text(json.dumps({"rss": {"feeds": []}}))
    assert load_sources(file)["rss"]["feeds"] == []
    assert "api_url" in load_sources(file)["platforms"]


def test_subprocess_cli_works_from_an_unrelated_directory_without_checkout(tmp_path):
    # A local HTTP server supplies real source responses to the actual child process.
    # No external network, model, YAML, or TrendRadar import is used.
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = RSS if self.path == "/feed" else json.dumps({"status": "success", "items": [
                {"title": "NVIDIA local source", "url": "https://example.com/local"},
            ]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        config = {"platforms": {"api_url": base + "/api", "sources": [{"id": "local"}]},
                  "rss": {"feeds": [{"id": "feed", "url": base + "/feed"}]}}
        file = tmp_path / "sources.json"
        file.write_text(json.dumps(config))
        result = tmp_path / "result.json"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "NO_PROXY": "127.0.0.1"}
        subprocess.run([sys.executable, "-m", "tradingagents.newsradar", "--output-dir", str(tmp_path / "data"),
                        "--config-file", str(file), "--result-file", str(result)], cwd=tmp_path, env=env,
                       check=True, capture_output=True, text=True, timeout=20)
        counts = json.loads(result.read_text())
        assert counts["hotlist"]["items"] == counts["rss"]["items"] == 1
        assert "trendradar" not in sys.modules
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

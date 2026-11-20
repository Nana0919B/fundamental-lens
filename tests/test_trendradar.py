"""Optional local news: real SQLite fixtures, frozen evidence and analyst wiring."""

import copy
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

from tradingagents.dataflows.research_store import ResearchStore
from tradingagents.dataflows.trendradar_adapter import (
    build_news_context,
    news_context_appendix,
    news_context_text,
    validate_news_context,
)

NOW = datetime(2026, 1, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
DAY = "2026-01-09"


@pytest.fixture
def radar(tmp_path):
    root = tmp_path / "radar"
    (root / "news").mkdir(parents=True)
    path = root / "news" / f"{DAY}.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE platforms(id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE news_items(id INTEGER PRIMARY KEY,title TEXT,platform_id TEXT,
                url TEXT,first_crawl_time TEXT,last_crawl_time TEXT,updated_at TEXT);
            CREATE TABLE rank_history(news_item_id INTEGER,rank INTEGER,crawl_time TEXT);
            CREATE TABLE crawl_records(id INTEGER,crawl_time TEXT);
            CREATE TABLE crawl_source_status(crawl_record_id INTEGER,platform_id TEXT,status TEXT);
            INSERT INTO platforms VALUES ('tech','科技新闻');
            INSERT INTO crawl_records VALUES (1,'10:00');
            INSERT INTO crawl_source_status VALUES (1,'tech','success');
            INSERT INTO news_items VALUES
                (1,'英伟达发布推理芯片','tech','https://example.com/chip','09:00','10:00','2026-01-09 10:00:00'),
                (2,'无关的娱乐新闻','tech','https://example.com/other','10:00','10:00','2026-01-09 10:00:00'),
                (3,'NVIDIA future event','tech','https://example.com/future','13:00','13:00','2026-01-09 13:00:00'),
                (4,'NVIDIA revised after cutoff','tech','https://example.com/revised','09:00','13:00','2026-01-09 13:00:00');
            INSERT INTO rank_history VALUES (1,8,'09:00'),(1,2,'10:00'),(1,1,'13:00');
        """)
    cfg = {"trendradar_enabled": True, "trendradar_data_dir": str(root),
           "trendradar_lookback_days": 1, "data_cache_dir": str(tmp_path / "cache")}
    return cfg, path


def freeze(cfg, ticker="NVDA", day=DAY):
    return build_news_context(cfg, ticker, day, now=NOW)


def test_rule_recall_clips_future_rows_revisions_and_rank_history(radar):
    cfg, path = radar
    before = path.read_bytes()
    context = freeze(cfg)
    assert [item["title"] for item in context["items"]] == ["英伟达发布推理芯片"]
    item = context["items"][0]
    assert item["published_at"] is None
    assert [r["rank"] for r in item["rank_history"]] == [8, 2]
    assert item["first_observed_at"].endswith("+08:00")
    assert context["coverage"]["status"] == "partial"
    assert "rss/2026-01-09" in context["coverage"]["missing_partitions"]
    assert path.read_bytes() == before
    validate_news_context(context, "NVDA", DAY)


def test_disabled_does_not_touch_database(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("database was opened"))
    assert build_news_context({}, "NVDA", DAY) is None


def test_records_committed_after_cutoff_are_excluded_even_in_same_minute(radar):
    cfg, path = radar
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE rank_history ADD COLUMN created_at TEXT")
        db.execute("UPDATE rank_history SET created_at='2026-01-09 12:00:05' WHERE rank=2")
        db.execute("ALTER TABLE crawl_records ADD COLUMN created_at TEXT")
        db.execute("INSERT INTO crawl_records VALUES (2,'12:00','2026-01-09 12:00:05')")
        db.execute("INSERT INTO crawl_source_status VALUES (2,'future-source','failed')")
    context = freeze(cfg)
    assert [r["rank"] for r in context["items"][0]["rank_history"]] == [8]
    assert "future-source" not in context["coverage"]["sources_failed"]


def test_missing_corrupt_stale_and_failed_sources_are_not_an_absence(radar):
    cfg, path = radar
    with sqlite3.connect(path) as db:
        db.execute("UPDATE crawl_source_status SET status='failed'")
    context = freeze(cfg)
    assert context["coverage"]["status"] == "unavailable"
    assert context["coverage"]["sources_failed"] == ["tech"]
    assert context["items"] == []
    path.write_bytes(b"not sqlite")
    context = freeze(cfg)
    assert "unreadable" in context["coverage"]["reason"]
    path.unlink()
    context = freeze(cfg)
    assert context["coverage"]["status"] == "unavailable"
    assert not path.exists()  # mode=ro never creates a replacement


def test_historical_databases_are_not_used_even_when_present(radar, monkeypatch):
    cfg, _ = radar
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("historical DB was opened"))
    context = freeze(cfg, day="2026-01-08")
    assert context["items"] == []
    assert context["coverage"]["status"] == "unavailable"
    assert "historical" in context["coverage"]["reason"]


def test_alias_boundaries_custom_groups_and_dedup(radar):
    cfg, path = radar
    with sqlite3.connect(path) as db:
        db.execute("UPDATE news_items SET title='NVDA another source' WHERE id=2")
        db.execute("UPDATE news_items SET url='https://example.com/chip' WHERE id=2")
    assert len(freeze(cfg)["items"]) == 1
    assert freeze(cfg, ticker="VDA")["items"] == []
    cfg["trendradar_terms"] = {"OTHER": {"industry": ["芯片"]}}
    context = freeze(cfg, ticker="OTHER")
    assert context["items"][0]["relation_type"] == "industry"
    cfg["trendradar_article_limit"] = 1
    assert len(freeze(cfg)["items"]) == 1


def test_rss_publication_times_and_failed_source_coverage(radar):
    cfg, _ = radar
    root = Path(cfg["trendradar_data_dir"])
    (root / "rss").mkdir()
    with sqlite3.connect(root / "rss" / f"{DAY}.db") as db:
        db.executescript("""
            CREATE TABLE rss_feeds(id TEXT,name TEXT);
            CREATE TABLE rss_items(id INTEGER,title TEXT,feed_id TEXT,url TEXT,
                first_crawl_time TEXT,published_at TEXT,summary TEXT,updated_at TEXT);
            CREATE TABLE rss_crawl_records(id INTEGER,crawl_time TEXT);
            CREATE TABLE rss_crawl_status(crawl_record_id INTEGER,feed_id TEXT,status TEXT);
            INSERT INTO rss_feeds VALUES ('rss','RSS source');
            INSERT INTO rss_crawl_records VALUES (1,'10:30');
            INSERT INTO rss_crawl_status VALUES (1,'rss','success'),(1,'broken','failed');
            INSERT INTO rss_items VALUES
                (1,'NVIDIA RSS','rss','https://example.com/rss','10:00','2026-01-09T09:00:00+08:00','Actual summary','2026-01-09 10:00:00'),
                (2,'NVIDIA future publication','rss','https://example.com/rss-future','10:00','2026-01-09T19:00:00+08:00','Future','2026-01-09 10:00:00');
        """)
    context = freeze(cfg)
    rss = [i for i in context["items"] if i["source_type"] == "rss"]
    assert len(rss) == 1
    assert rss[0]["summary"] == "Actual summary"
    assert rss[0]["evidence_id"] != context["items"][-1]["evidence_id"]
    assert context["coverage"]["sources_failed"] == ["broken"]


def test_archive_is_immutable_and_replay_does_not_query_live_data(radar, tmp_path):
    cfg, path = radar
    context = freeze(cfg)
    store = ResearchStore(tmp_path / "archive.db")
    key = store.archive_news_context(context)
    path.unlink()
    assert store.load_news_context(key) == context
    corrupt = copy.deepcopy(context)
    corrupt["items"][0]["title"] = "changed"
    with pytest.raises(ValueError, match="digest"):
        store.archive_news_context(corrupt)
    with pytest.raises(ValueError):
        validate_news_context(context, "MSFT", DAY)
    assert store.load_news_context(key) == context




def test_no_executable_source_link_is_rendered(radar):
    context = freeze(radar[0])
    context["items"][0]["url"] = "javascript:alert(1)"
    assert "javascript:" not in news_context_appendix(context)
    assert news_context_text(None) == news_context_appendix(None) == ""

"""Write daily SQLite evidence compatible with the existing read-only adapter."""

import sqlite3
from contextlib import closing
from pathlib import Path


def _schema(db, kind):
    if kind == "hotlist":
        db.executescript("""
            CREATE TABLE IF NOT EXISTS platforms(id TEXT PRIMARY KEY,name TEXT,is_active INTEGER DEFAULT 1,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS news_items(
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, platform_id TEXT NOT NULL,
                rank INTEGER NOT NULL, url TEXT DEFAULT '', mobile_url TEXT DEFAULT '',
                first_crawl_time TEXT NOT NULL, last_crawl_time TEXT NOT NULL,
                crawl_count INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS rank_history(
                id INTEGER PRIMARY KEY,news_item_id INTEGER NOT NULL,rank INTEGER NOT NULL,
                crawl_time TEXT NOT NULL,created_at TEXT);
            CREATE INDEX IF NOT EXISTS newsradar_source_url ON news_items(platform_id,url);
            CREATE INDEX IF NOT EXISTS newsradar_ranks ON rank_history(news_item_id,crawl_time);
            CREATE TABLE IF NOT EXISTS crawl_records(
                id INTEGER PRIMARY KEY,crawl_time TEXT UNIQUE,total_items INTEGER DEFAULT 0,created_at TEXT);
            CREATE TABLE IF NOT EXISTS crawl_source_status(
                crawl_record_id INTEGER,platform_id TEXT,status TEXT,
                PRIMARY KEY(crawl_record_id,platform_id));
        """)
    else:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS rss_feeds(
                id TEXT PRIMARY KEY,name TEXT,feed_url TEXT DEFAULT '',is_active INTEGER DEFAULT 1,
                last_fetch_time TEXT,last_fetch_status TEXT,item_count INTEGER DEFAULT 0,
                created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS rss_items(
                id INTEGER PRIMARY KEY,title TEXT NOT NULL,feed_id TEXT NOT NULL,url TEXT NOT NULL,
                guid TEXT DEFAULT '',published_at TEXT,summary TEXT,author TEXT,
                first_crawl_time TEXT NOT NULL,last_crawl_time TEXT NOT NULL,
                crawl_count INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT);
            CREATE INDEX IF NOT EXISTS newsradar_feed_url ON rss_items(feed_id,url);
            CREATE TABLE IF NOT EXISTS rss_crawl_records(
                id INTEGER PRIMARY KEY,crawl_time TEXT UNIQUE,total_items INTEGER DEFAULT 0,created_at TEXT);
            CREATE TABLE IF NOT EXISTS rss_crawl_status(
                crawl_record_id INTEGER,feed_id TEXT,status TEXT,error_message TEXT,
                PRIMARY KEY(crawl_record_id,feed_id));
        """)


def save_source(root, kind, source, items, observed_at, *, succeeded=True):
    if observed_at.tzinfo is None:
        raise ValueError("Collection time must include the configured timezone")
    folder = Path(root) / ("news" if kind == "hotlist" else "rss")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{observed_at:%Y-%m-%d}.db"
    clock = observed_at.strftime("%H:%M:%S.%f")
    stamp = observed_at.replace(tzinfo=None).isoformat(sep=" ")
    source_id, name = source["id"], source.get("name", source["id"])
    with closing(sqlite3.connect(path, timeout=2)) as db:
        _schema(db, kind)
        with db:
            if kind == "hotlist":
                db.execute("INSERT INTO platforms(id,name,updated_at) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at", (source_id, name, stamp))
                _hotlist(db, source_id, items if succeeded else [], clock, stamp, succeeded)
                records, statuses, source_col = "crawl_records", "crawl_source_status", "platform_id"
            else:
                db.execute("""INSERT INTO rss_feeds(id,name,feed_url,last_fetch_time,last_fetch_status,item_count,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,feed_url=excluded.feed_url,last_fetch_time=excluded.last_fetch_time,
                    last_fetch_status=excluded.last_fetch_status,item_count=excluded.item_count,updated_at=excluded.updated_at""",
                           (source_id, name, source.get("url", ""), clock, "success" if succeeded else "failed", len(items), stamp, stamp))
                if succeeded:
                    _rss(db, source_id, items, clock, stamp)
                records, statuses, source_col = "rss_crawl_records", "rss_crawl_status", "feed_id"
            db.execute(f"INSERT INTO {records}(crawl_time,total_items,created_at) VALUES (?,?,?) ON CONFLICT(crawl_time) DO UPDATE SET total_items=total_items+excluded.total_items", (clock, len(items) if succeeded else 0, stamp))
            record = db.execute(f"SELECT id FROM {records} WHERE crawl_time=?", (clock,)).fetchone()[0]
            db.execute(f"INSERT INTO {statuses}(crawl_record_id,{source_col},status) VALUES (?,?,?) ON CONFLICT(crawl_record_id,{source_col}) DO UPDATE SET status=excluded.status", (record, source_id, "success" if succeeded else "failed"))
    return path


def _rank(db, news_id, rank, clock, stamp):
    if not db.execute("SELECT 1 FROM rank_history WHERE news_item_id=? AND crawl_time=? AND rank=?", (news_id, clock, rank)).fetchone():
        db.execute("INSERT INTO rank_history(news_item_id,rank,crawl_time,created_at) VALUES (?,?,?,?)", (news_id, rank, clock, stamp))


def _hotlist(db, source, items, clock, stamp, succeeded):
    if not succeeded:
        return
    seen = set()
    for item in items:
        key, value = ("url", item["url"]) if item["url"] else ("title", item["title"])
        old = db.execute(f"SELECT id,last_crawl_time FROM news_items WHERE platform_id=? AND {key}=? LIMIT 1", (source, value)).fetchone()
        if old:
            news_id = old[0]
            db.execute("UPDATE news_items SET title=?,rank=?,url=?,mobile_url=?,last_crawl_time=?,crawl_count=crawl_count+?,updated_at=? WHERE id=?", (item["title"], item["rank"], item["url"], item.get("mobile_url", ""), clock, int(old[1] != clock), stamp, news_id))
        else:
            news_id = db.execute("""INSERT INTO news_items(title,platform_id,rank,url,mobile_url,
                first_crawl_time,last_crawl_time,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                                 (item["title"], source, item["rank"], item["url"], item.get("mobile_url", ""), clock, clock, stamp, stamp)).lastrowid
        seen.add(news_id)
        _rank(db, news_id, item["rank"], clock, stamp)
    for news_id, in db.execute("SELECT id FROM news_items WHERE platform_id=? AND rank>0", (source,)).fetchall():
        if news_id not in seen:
            db.execute("UPDATE news_items SET rank=0,updated_at=? WHERE id=?", (stamp, news_id))
            _rank(db, news_id, 0, clock, stamp)


def _rss(db, source, items, clock, stamp):
    for item in items:
        old = None
        if item.get("guid"):
            old = db.execute("SELECT id,last_crawl_time FROM rss_items WHERE feed_id=? AND guid=? LIMIT 1", (source, item["guid"])).fetchone()
        old = old or db.execute("SELECT id,last_crawl_time FROM rss_items WHERE feed_id=? AND url=? LIMIT 1", (source, item["url"])).fetchone()
        values = (item["title"], item["url"], item.get("guid", ""), item.get("published_at"), item.get("summary", ""), item.get("author", ""))
        if old:
            db.execute("UPDATE rss_items SET title=?,url=?,guid=?,published_at=?,summary=?,author=?,last_crawl_time=?,crawl_count=crawl_count+?,updated_at=? WHERE id=?", (*values, clock, int(old[1] != clock), stamp, old[0]))
        else:
            db.execute("""INSERT INTO rss_items(title,url,guid,published_at,summary,author,
                feed_id,first_crawl_time,last_crawl_time,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (*values, source, clock, clock, stamp, stamp))

"""Append-only SQLite archive of research snapshots, facts and report versions."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class ResearchStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=20)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, as_of_date TEXT NOT NULL,
                    retrieved_at TEXT, source TEXT, digest TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshots_ticker ON snapshots(ticker, retrieved_at);
                CREATE TABLE IF NOT EXISTS facts (
                    snapshot_id TEXT, evidence_id TEXT, period TEXT, field TEXT, value_json TEXT,
                    frequency TEXT, currency TEXT, source TEXT,
                    PRIMARY KEY(snapshot_id, evidence_id));
                CREATE TABLE IF NOT EXISTS reports (
                    snapshot_id TEXT PRIMARY KEY, ticker TEXT, digest TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS report_versions (
                    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, parent_run_id TEXT,
                    ticker TEXT NOT NULL, created_at TEXT NOT NULL, digest TEXT NOT NULL,
                    macro_payload TEXT NOT NULL, report_payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS peer_pools (
                    ticker TEXT PRIMARY KEY, peers TEXT NOT NULL);
            """)
            yield db

    def archive_snapshot(self, snapshot):
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        company = snapshot["company"]
        with self.connect() as db:
            prior = db.execute(
                "SELECT digest FROM snapshots WHERE id=?", (snapshot["id"],)
            ).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise ValueError("An archived snapshot cannot be overwritten")
                return digest
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot["id"],
                    company["symbol"],
                    snapshot["as_of_date"],
                    company.get("retrieved_at"),
                    company.get("source"),
                    digest,
                    raw,
                ),
            )
            db.executemany(
                "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        snapshot["id"],
                        f["id"],
                        f.get("period"),
                        f["field"],
                        json.dumps(f["value"], ensure_ascii=False),
                        f.get("frequency"),
                        f.get("currency"),
                        f.get("source"),
                    )
                    for f in snapshot.get("evidence", [])
                ],
            )
        return digest

    def archive_reports(self, snapshot, reports):
        raw = json.dumps(reports, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            prior = db.execute(
                "SELECT digest FROM reports WHERE snapshot_id=?", (snapshot["id"],)
            ).fetchone()
            if prior and prior["digest"] != digest:
                raise ValueError("Create a new research version instead of replacing reports")
            db.execute(
                "INSERT OR IGNORE INTO reports VALUES (?,?,?,?)",
                (snapshot["id"], snapshot["company"]["symbol"], digest, raw),
            )

    def archive_report_version(self, run_id, snapshot, macro, reports, parent_run_id, created_at):
        raw = json.dumps(reports, ensure_ascii=False, sort_keys=True, allow_nan=False)
        macro_raw = json.dumps(macro, ensure_ascii=False, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256((macro_raw + "\n" + raw).encode()).hexdigest()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM snapshots WHERE id=?", (snapshot["id"],)).fetchone():
                raise ValueError("The financial snapshot must already be archived")
            prior = db.execute(
                "SELECT digest FROM report_versions WHERE id=?", (run_id,)
            ).fetchone()
            if prior and prior["digest"] != digest:
                raise ValueError("A report version cannot be overwritten")
            db.execute(
                "INSERT OR IGNORE INTO report_versions VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    snapshot["id"],
                    parent_run_id,
                    snapshot["company"]["symbol"],
                    created_at,
                    digest,
                    macro_raw,
                    raw,
                ),
            )
        return digest

    def history(self, ticker):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT s.id,s.as_of_date,s.retrieved_at,s.source,s.digest, r.snapshot_id IS NOT NULL AS has_report FROM snapshots s LEFT JOIN reports r ON r.snapshot_id=s.id WHERE s.ticker=? UNION ALL SELECT v.id,s.as_of_date,v.created_at AS retrieved_at,s.source,v.digest,1 AS has_report FROM report_versions v JOIN snapshots s ON s.id=v.snapshot_id WHERE v.ticker=? ORDER BY retrieved_at DESC LIMIT 50",
                    (ticker, ticker),
                )
            ]

    def profile_changes(self, snapshot):
        company = snapshot["company"]
        with self.connect() as db:
            row = db.execute(
                "SELECT id,payload FROM snapshots WHERE ticker=? AND id<>? AND retrieved_at<=? ORDER BY retrieved_at DESC LIMIT 1",
                (company["symbol"], snapshot["id"], company.get("retrieved_at", "")),
            ).fetchone()
        if not row:
            return {"previous_id": None, "changes": []}
        previous = json.loads(row["payload"])["company"].get("profile", {})
        current = company.get("profile", {})
        keys = (
            "longName",
            "sector",
            "industry",
            "fullTimeEmployees",
            "companyOfficers",
            "heldPercentInsiders",
            "heldPercentInstitutions",
            "sharesOutstanding",
        )
        return {
            "previous_id": row["id"],
            "note": "仅为本地两次采集的差异，不代表公司披露的变更时间或财报更正。",
            "changes": [
                {"field": key, "before": previous.get(key), "after": current.get(key)}
                for key in keys
                if previous.get(key) != current.get(key)
            ],
        }

    def save_peers(self, ticker, peers):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO peer_pools VALUES (?,?)", (ticker, json.dumps(peers))
            )

    def peer_pool(self, ticker):
        with self.connect() as db:
            row = db.execute("SELECT peers FROM peer_pools WHERE ticker=?", (ticker,)).fetchone()
        return json.loads(row["peers"]) if row else []

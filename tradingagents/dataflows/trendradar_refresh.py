"""Best-effort refresh before freezing news, isolated from the analysis runtime."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.trendradar_adapter import _digest, _observed, _stored_time

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(UTC)


def _fresh_partitions(root, now, timezone, minutes, *, require_both=True):
    """A recent success for both hotlist and RSS; failures/mtime never count."""
    day = now.astimezone(timezone).date()
    fresh = set()
    for folder, records, statuses in (
        ("news", "crawl_records", "crawl_source_status"),
        ("rss", "rss_crawl_records", "rss_crawl_status"),
    ):
        for date in (day, day - timedelta(days=1)):
            path = root / folder / f"{date}.db"
            if not path.is_file():
                continue
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                    db.row_factory = sqlite3.Row
                    rows = db.execute(f"""SELECT r.* FROM {records} r
                        WHERE EXISTS (SELECT 1 FROM {statuses} s
                            WHERE s.crawl_record_id=r.id AND s.status='success')""").fetchall()
                    for raw in rows:
                        row = dict(raw)
                        stamp = _observed(date, row["crawl_time"], timezone)
                        if row.get("created_at"):
                            stamp = max(stamp, _stored_time(row["created_at"], timezone))
                        if timedelta(0) <= now - stamp <= timedelta(minutes=minutes):
                            fresh.add(folder)
            except (sqlite3.Error, ValueError, TypeError):
                continue
    return fresh == {"news", "rss"} if require_both else bool(fresh)


def _recent_state(path, now, minutes, failure_minutes):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        age = now - datetime.fromisoformat(state["finished_at"])
        budget = minutes if state["status"] == "refreshed" else failure_minutes
        if timedelta(0) <= age < timedelta(minutes=budget):
            return {**state, "status": "reused" if state["status"] == "refreshed" else "cooldown"}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def ensure_news_fresh(config, trade_date):
    """Bounded subprocess, shared nonblocking lock and failure backoff. Never raises."""
    if not config.get("trendradar_enabled") or not config.get("trendradar_auto_refresh", True):
        return {"status": "disabled"}
    try:
        now = utc_now()
        timezone = ZoneInfo(config.get("trendradar_timezone", "Asia/Shanghai"))
        if trade_date != now.astimezone(timezone).date().isoformat():
            return {"status": "historical", "reason": "Historical runs never collect live news."}
        if not config.get("trendradar_data_dir"):
            return {"status": "unavailable", "reason": "TrendRadar data directory is not configured."}
        root = Path(config["trendradar_data_dir"]).expanduser().resolve()
        minutes = max(1, int(config.get("trendradar_refresh_minutes", 30)))
        cooldown = max(1, int(config.get("trendradar_refresh_failure_minutes", 5)))
        if _fresh_partitions(root, now, timezone, minutes):
            return {"status": "reused", "reason": "Recent successful collection already exists."}
        project = Path(config.get("trendradar_project_dir") or root.parent).expanduser().resolve()
        if not (project / "trendradar/crawler/fetcher.py").is_file():
            return {"status": "unavailable", "reason": "Optional TrendRadar checkout is missing; original news tools remain available."}
        metadata = root / ".tradingagents-refresh"
        metadata.mkdir(parents=True, exist_ok=True)
        state_path = metadata / "state.json"
        with locked(metadata / "collector", wait=False) as acquired:
            if not acquired:
                return {"status": "busy", "reason": "Another run is refreshing; using available data."}
            now = utc_now()
            if _fresh_partitions(root, now, timezone, minutes):
                return {"status": "reused", "reason": "Another run supplied fresh data."}
            prior = _recent_state(state_path, now, minutes, cooldown)
            if prior and (prior["status"] != "reused"
                          or _fresh_partitions(root, now, timezone, minutes, require_both=False)):
                return prior
            result_file = metadata / "result.json"
            result_file.unlink(missing_ok=True)
            # Absolute installed module path also works with a dedicated collector
            # interpreter that has dependencies but not the TradingAgents package.
            command = [str(config.get("trendradar_python") or sys.executable),
                       str(Path(__file__).with_name("trendradar_collect.py")), "--project", str(project),
                       "--output-dir", str(root), "--timezone", str(timezone),
                       "--result-file", str(result_file)]
            platforms = config.get("trendradar_refresh_platforms", ["wallstreetcn-hot", "cls-hot", "zhihu"])
            if platforms:
                command.extend(["--platforms", *platforms])
            state = {"status": "failed", "reason": "Collector failed; original news tools remain available."}
            try:
                with (metadata / "collector.log").open("w", encoding="utf-8") as log:
                    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                               timeout=max(1, min(int(config.get("trendradar_refresh_timeout", 60)), 300)),
                                               check=False)
                if completed.returncode == 0:
                    counts = json.loads(result_file.read_text(encoding="utf-8"))
                    if not isinstance(counts, dict) or not any(v.get("items", 0) > 0 for v in counts.values()):
                        raise ValueError("No collected items")
                    state = {"status": "refreshed", "counts": counts}
            except subprocess.TimeoutExpired:
                state = {"status": "timeout", "reason": "Collection timed out; using available data and original news tools."}
            except (OSError, ValueError, TypeError):
                pass
            state["finished_at"] = utc_now().isoformat()
            replace_file(state_path, lambda path: Path(path).write_text(json.dumps(state), encoding="utf-8"))
            return state
    except Exception as exc:  # Optional enrichment must never block company-news analysis.
        logger.warning("TrendRadar refresh skipped (%s)", type(exc).__name__)
        return {"status": "unavailable", "reason": "Optional refresh unavailable; original news tools remain available."}


def prepare_news_context(config, ticker, trade_date, identity=None):
    """Refresh first, then timestamp the frozen context so new rows are included."""
    from tradingagents.dataflows.trendradar_adapter import build_news_context

    if not config.get("trendradar_enabled"):
        return None
    refresh = ensure_news_fresh(config, trade_date)
    try:
        context = build_news_context(config, ticker, trade_date, identity)
    except Exception as exc:
        logger.warning("TrendRadar evidence skipped (%s)", type(exc).__name__)
        return None
    if context is not None:
        context["refresh"] = refresh
        context["snapshot_id"] = _digest(context)
    return context

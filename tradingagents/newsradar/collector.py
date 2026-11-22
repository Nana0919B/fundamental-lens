"""Collect source-by-source so one broken feed cannot discard successful sources."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .parsing import http_url, parse_feed, parse_hotlist
from .sources import load_sources
from .storage import save_source

logger = logging.getLogger(__name__)


def _get(session, url, **kwargs):
    if not http_url(url):
        raise ValueError("Invalid source URL")
    for attempt in range(2):
        try:
            response = session.get(url, timeout=(3, 10), **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt:
                raise
    raise RuntimeError("Unreachable")


def collect_once(output_dir, *, timezone="Asia/Shanghai", config_file=None, platforms=None,
                 session=None, clock=None):
    config = load_sources(config_file)
    zone = ZoneInfo(timezone)
    clock = clock or (lambda: datetime.now(zone))
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": "TradingAgents-NewsRadar/1.0", "Accept": "application/json, application/rss+xml, application/atom+xml, */*"})
    counts = {}
    try:
        for kind, section, key in (("hotlist", "platforms", "sources"), ("rss", "rss", "feeds")):
            settings = config[section]
            if not settings.get("enabled", True):
                continue
            sources = [s for s in settings.get(key, []) if s.get("enabled", True)]
            if kind == "hotlist" and platforms is not None:
                unknown = set(platforms) - {s["id"] for s in sources}
                if unknown:
                    raise ValueError("Requested platforms are not configured: " + ", ".join(sorted(unknown)))
                sources = [s for s in sources if s["id"] in platforms]
            result = counts[kind] = {"items": 0, "failed": []}
            for source in sources:
                items, succeeded = [], True
                try:
                    if kind == "hotlist":
                        # This public endpoint requires a browser-compatible user agent;
                        # RSS providers receive the identified collector user agent instead.
                        response = _get(session, settings["api_url"], params={"id": source["id"], "latest": ""},
                                        headers={"User-Agent": "Mozilla/5.0"})
                        items = parse_hotlist(response.json(), source.get("expected_domain", ""))
                    else:
                        response = _get(session, source["url"])
                        items = parse_feed(response.content, max(1, min(int(source.get("max_items", 100)), 1000)))
                except Exception as exc:
                    succeeded = False
                    logger.warning("NewsRadar source %s failed (%s)", source["id"], type(exc).__name__)
                try:
                    save_source(output_dir, kind, source, items, clock().astimezone(zone), succeeded=succeeded)
                except Exception as exc:
                    succeeded = False
                    logger.warning("NewsRadar source %s storage failed (%s)", source["id"], type(exc).__name__)
                if succeeded:
                    result["items"] += len(items)
                else:
                    result["failed"].append(source["id"])
    finally:
        if own_session:
            session.close()
    return counts

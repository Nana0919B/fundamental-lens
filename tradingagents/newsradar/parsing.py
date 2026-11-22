"""Normalize public feed payloads without inferring missing publication dates."""

import calendar
import html
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import feedparser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value, limit):
    parser = _Text()
    parser.feed(str(value or ""))
    parser.close()
    return html.unescape(" ".join(" ".join(parser.parts).split()))[:limit]


def http_url(value, domain=""):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if domain and (parsed.scheme != "https" or not (host == domain or host.endswith("." + domain))):
            return ""
        if any(c.isspace() or ord(c) < 32 for c in value):
            return ""
        return value
    except ValueError:
        return ""


def publication_time(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    # Public feeds sometimes omit the offset. Treat those timestamps as UTC,
    # consistently with feedparser, rather than the host's local timezone.
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).isoformat()


def parse_hotlist(payload, domain=""):
    if not isinstance(payload, dict) or payload.get("status") not in {"success", "cache"}:
        raise ValueError("Hotlist API did not return success/cache")
    if not isinstance(payload.get("items"), list):
        raise ValueError("Hotlist items must be an array")
    items = []
    for rank, item in enumerate(payload["items"][:1000], 1):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        title = plain_text(item["title"], 1000)
        url = http_url(item.get("url", ""), domain)
        mobile = http_url(item.get("mobileUrl", ""), domain)
        if domain and any(item.get(k) and not http_url(item[k], domain) for k in ("url", "mobileUrl")):
            raise ValueError("Hotlist link does not match its source domain")
        if title:
            items.append({"title": title, "url": url, "mobile_url": mobile, "rank": rank})
    return items


def parse_feed(content, max_items=100):
    text = content
    if isinstance(content, bytes) and content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
        text = content.decode("utf-8-sig")
    if isinstance(text, str):
        text = text.lstrip("\ufeff \t\r\n")
    if isinstance(text, str) and text.lstrip().startswith("{"):
        payload = json.loads(text)
        if "jsonfeed.org/version/" not in str(payload.get("version", "")) or not isinstance(payload.get("items"), list):
            raise ValueError("Invalid JSON Feed")
        entries = payload["items"]
        json_feed = True
    else:
        payload = feedparser.parse(content)
        if payload.bozo and not payload.entries:
            raise ValueError("Invalid RSS/Atom feed")
        entries = payload.entries
        json_feed = False
    result = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        url = http_url(item.get("url") or item.get("external_url")) if json_feed else http_url(item.get("link"))
        title = plain_text(item.get("title"), 1000)
        if not url or not title:
            continue
        published = publication_time(item.get("date_published") if json_feed else item.get("published") or item.get("updated"))
        parsed = item.get("published_parsed") or item.get("updated_parsed")
        if not published and parsed and not json_feed:
            published = datetime.fromtimestamp(calendar.timegm(parsed), UTC).isoformat()
        result.append({"title": title, "url": url, "guid": str(item.get("id") or item.get("guid") or ""),
                       "published_at": published,
                       "summary": plain_text(item.get("summary") or item.get("content_text") or item.get("content_html"), 1200),
                       "author": str(item.get("author") or "") if not json_feed else ""})
        if len(result) >= max_items:
            break
    return result

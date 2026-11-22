"""Bundled public sources; optional overrides are ordinary JSON, not repo paths."""

import copy
import json
from pathlib import Path

DEFAULT_SOURCES = {
    "platforms": {
        "enabled": True,
        "api_url": "https://newsnow.busiyi.world/api/s",
        "sources": [
            {"id": "wallstreetcn-hot", "name": "华尔街见闻", "expected_domain": "wallstreetcn.com"},
            {"id": "cls-hot", "name": "财联社热门", "expected_domain": "cls.cn"},
            {"id": "zhihu", "name": "知乎", "expected_domain": "zhihu.com"},
        ],
    },
    "rss": {
        "enabled": True,
        "feeds": [
            {"id": "hacker-news", "name": "Hacker News", "url": "https://news.ycombinator.com/rss"},
            {"id": "yahoo-finance", "name": "雅虎财经 · AAPL", "url": "https://finance.yahoo.com/rss/headline?s=AAPL"},
        ],
    },
}


def load_sources(path=None):
    if path is None:
        return copy.deepcopy(DEFAULT_SOURCES)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("NewsRadar sources must be a JSON object")
    # Explicit sections replace defaults, so removing a feed cannot silently re-enable it.
    result = copy.deepcopy(DEFAULT_SOURCES)
    for key in ("platforms", "rss"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError(f"{key} must be an object")
            result[key].update(data[key])
    return result

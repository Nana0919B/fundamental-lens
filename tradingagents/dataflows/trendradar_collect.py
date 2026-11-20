"""Collect once using a separate TrendRadar checkout; never run AI or notifications.

Use the checkout's installed environment, or the TradingAgents ``trendradar``
extra for checkouts with lazy AppContext imports. No AI models are called.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--platforms", nargs="*")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timezone")
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.project.resolve()))
    import yaml
    from trendradar.crawler import DataFetcher
    from trendradar.crawler.rss import RSSFetcher
    from trendradar.storage import LocalStorageBackend, convert_crawl_results_to_news_data

    config = yaml.safe_load((args.project / "config/config.yaml").read_text())
    timezone = args.timezone or config.get("app", {}).get("timezone", "Asia/Shanghai")
    now = datetime.now(ZoneInfo(timezone))
    storage = LocalStorageBackend(str(args.output_dir or args.project / "output"), False, False, timezone)
    counts = {}
    try:
        platforms = config.get("platforms", {})
        if platforms.get("enabled", True):
            sources = platforms.get("sources", [])
            if args.platforms is not None:
                sources = [s for s in sources if s["id"] in args.platforms]
            if sources:
                results, names, failed = DataFetcher(api_url=platforms.get("api_url") or None).crawl_websites(
                    [(s["id"], s.get("name", s["id"])) for s in sources], 1000,
                    domain_rules={s["id"]: s["expected_domain"] for s in sources if s.get("expected_domain")},
                )
                data = convert_crawl_results_to_news_data(
                    results, names, failed, now.strftime("%H:%M"), now.strftime("%Y-%m-%d"),
                )
                if not storage.save_news_data(data):
                    raise RuntimeError("Could not save hotlist data")
                counts["hotlist"] = {"items": sum(len(v) for v in results.values()), "failed": failed}
        rss = dict(config.get("rss", {}))
        if rss.get("enabled", False):
            rss.update(timezone=timezone, request_interval=1000, timeout=15)
            data = RSSFetcher.from_config(rss).fetch_all()
            if not storage.save_rss_data(data):
                raise RuntimeError("Could not save RSS data")
            counts["rss"] = {"items": sum(len(v) for v in data.items.values()), "failed": data.failed_ids}
    finally:
        storage.cleanup()
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    if args.result_file:
        args.result_file.write_text(json.dumps(counts), encoding="utf-8")
    if not any(c["items"] for c in counts.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

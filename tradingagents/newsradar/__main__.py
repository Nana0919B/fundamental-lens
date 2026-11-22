"""Collect bundled hotlist/RSS sources without an external source checkout."""

import argparse
import json
import logging
from pathlib import Path

from .collector import collect_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".tradingagents/newsradar")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--platforms", nargs="*")
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    counts = collect_once(args.output_dir, timezone=args.timezone, config_file=args.config_file, platforms=args.platforms)
    output = json.dumps(counts, ensure_ascii=False, indent=2)
    print(output)
    if args.result_file:
        args.result_file.write_text(output, encoding="utf-8")
    if not any(result["items"] for result in counts.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

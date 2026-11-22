# Built-in NewsRadar news enrichment

TradingAgents includes `tradingagents.newsradar`: hotlist requests, RSS/Atom/JSON
Feed parsing, daily SQLite storage and rank history. It is an independent,
compact implementation of the public source interfaces and the existing news
evidence database contract; it does not import or copy the external TrendRadar
application. No second GitHub repository, YAML configuration, separate Python
interpreter, LiteLLM, MCP service or AI classifier is required.

Normal installation includes its `requests` and `feedparser` dependencies:

```sh
python -m pip install -e .
```

The old `.[trendradar]` extra remains an empty compatibility alias. Live
collection still requires network access to the configured news/RSS providers.

## Enable collection

Add to `.env`, then restart an already-running CLI or Web service:

```dotenv
TRADINGAGENTS_TRENDRADAR_ENABLED=true
```

The existing `TRADINGAGENTS_TRENDRADAR_*` configuration names and frozen snapshot
format remain supported. Default data storage is `~/.tradingagents/newsradar/`.
An optional override can point to any writable data directory, including existing
compatible daily databases; it does not need a repository beside it:

```dotenv
TRADINGAGENTS_TRENDRADAR_DATA_DIR=/absolute/path/to/news-data
TRADINGAGENTS_TRENDRADAR_TIMEZONE=Asia/Shanghai
TRADINGAGENTS_TRENDRADAR_ARTICLE_LIMIT=30
TRADINGAGENTS_TRENDRADAR_AUTO_REFRESH=true
TRADINGAGENTS_TRENDRADAR_REFRESH_MINUTES=30
TRADINGAGENTS_TRENDRADAR_REFRESH_TIMEOUT=60
```

`TRADINGAGENTS_TRENDRADAR_PROJECT_DIR` and `TRADINGAGENTS_TRENDRADAR_PYTHON` are
obsolete and ignored. Remove them from old environments. Existing archived
research and its frozen inputs do not need rewriting.

## Modules and data flow

```text
newsradar/sources.py    Bundled sources and optional JSON overrides
newsradar/parsing.py    Public API / RSS / Atom / JSON Feed normalization
newsradar/storage.py    Daily SQLite, URL/GUID identity and rank history
newsradar/collector.py  Per-source requests, retries and partial failure handling
newsradar/__main__.py  Standalone collection command
    -> trendradar_refresh.py  Freshness, lock, timeout and failure cooldown
    -> trendradar_adapter.py  Read-only recall and frozen news_context
    -> News Analyst           Unified report with original news/macro tools
```

The package is installed in the same wheel as TradingAgents. Its Python API:

```python
from tradingagents.newsradar import collect_once

counts = collect_once('/path/to/news-data', platforms=['cls-hot', 'zhihu'])
```

## Sources and customization

Bundled hotlists are 华尔街见闻 (`wallstreetcn-hot`), 财联社热门 (`cls-hot`)
and 知乎 (`zhihu`), requested through the public NewsNow API. Bundled RSS feeds
are Hacker News and Yahoo's **AAPL-specific** feed; the latter is not a complete
market-wide news source. Sources can fail or change independently of this code.

To add company-specific feeds or replace sources, create a local JSON file and
set `TRADINGAGENTS_TRENDRADAR_SOURCES_FILE=/absolute/path/to/sources.json`:

```json
{
  "platforms": {
    "enabled": true,
    "api_url": "https://newsnow.busiyi.world/api/s",
    "sources": [
      {"id": "cls-hot", "name": "财联社热门", "expected_domain": "cls.cn"}
    ]
  },
  "rss": {
    "enabled": true,
    "feeds": [
      {"id": "yahoo-msft", "name": "Yahoo MSFT", "url": "https://finance.yahoo.com/rss/headline?s=MSFT"}
    ]
  }
}
```

Source lists replace the corresponding bundled list. Do not request platform
IDs absent from your list: set programmatic `trendradar_refresh_platforms=[]`
to collect all configured platforms, or select IDs present in the file. Each
source can have `enabled: false`. RSS accepts `max_items` (default 100).
Malformed configuration is reported as a collection failure; existing company
news tools continue normally. No external repository configuration is read.

## Manual collection

```sh
python -m tradingagents.newsradar --platforms wallstreetcn-hot cls-hot zhihu
python -m tradingagents.newsradar --output-dir /path/to/news-data --config-file /path/to/sources.json
```

`tradingagents-newsradar` is the installed console entry point. The earlier
`python -m tradingagents.dataflows.trendradar_collect` and repository script
`scripts/collect_trendradar.py` remain wrappers for the same built-in command.
The old `--project` argument is removed. Collection only writes news data; it
never sends notifications, calls an AI model, or regenerates research reports.

## Refresh and recovery

- New current-date research checks source freshness before preparing evidence.
  Successful hotlist and RSS observations within 30 minutes are reused, even
  across midnight. File modification times and failed attempts do not count.
- Otherwise a bounded subprocess runs the installed collector. Each successful
  source is committed separately; a later broken source or timeout cannot roll
  back earlier successes. Rank 0 records a successfully observed off-list item;
  a failed source does not imply that its previous items left the chart.
- Default timeout is 60 seconds. A shared nonblocking file lock avoids duplicate
  collectors; competing runs continue with available data. Failures have a
  5-minute cooldown. Successful partial collection is reused for 30 minutes.
- If refreshing fails, cached data within the existing 24-hour fallback budget
  may still be used. Otherwise evidence is unavailable. Original Yahoo/Alpha
  Vantage, FRED and prediction-market tools are not replaced.
- Frozen inputs, Web retries, checkpoint resumes and historical runs do not
  collect again. Fresh extraction from mutable daily databases is allowed only
  for the current date; historical replay requires a previously frozen context.
- The cutoff is taken after collection. Later revisions, publication times and
  rank observations are excluded. Hotlist first-observation time is explicitly
  distinguished from publication time. Missing offsets in feed dates are
  normalized to UTC, independently of the execution host.

Refresh state and the latest attempt's collector log live in
`<data_dir>/.tradingagents-refresh/`. Reports expose collection outcomes and
failed sources. An overall successful exit only means some source supplied
items. No cron or standing external service is required.

## Recall, snapshots and limits

Rule-based recall uses the resolved company name, ticker aliases and optional
per-ticker `trendradar_terms` groups (`direct`, `industry`, `macro`). ASCII terms
use word boundaries. Direct matches precede industry and macro matches, then
newer observations. Duplicate URLs retain other platform observations; events
under different URLs are not semantically clustered. No AI filtering is used.

The source adapter reads SQLite with `mode=ro` and a read transaction, retaining
up to 31 days per type, 10,000 rows per partition, 100 articles (default 30) and
24 rank observations per article. Daily databases are not one atomic global
snapshot. Coverage is always partial: sampled hotlists do not prove that no
other news exists, and rank measures attention rather than sentiment or returns.

`news_context` retains its schema version, ticker/date, cutoff, recall rules,
coverage, source-linked items and SHA-256 `snapshot_id`. The graph archives it
in `data_cache_dir/news.sqlite3`; Web jobs additionally persist it in their
research store before analysts run. Evidence IDs include date, source type,
platform and item ID. Original source files are not needed for archived replay:

```python
from tradingagents.dataflows.research_store import ResearchStore
from tradingagents.graph.trading_graph import TradingAgentsGraph

news = ResearchStore('/path/to/cache/news.sqlite3').load_news_context(snapshot_id)
graph = TradingAgentsGraph(config=config, news_context=news)
state, rating = graph.propagate(news['ticker'], news['trade_date'])
```

Only the NewsRadar supplement is frozen; other tools retain their own
historical limitations. Model output is not promised to be identical. The News
Analyst receives labelled evidence and appends deterministic references;
its `news_report` still feeds the existing research and risk workflow.

## Tests

```sh
python -m pytest tests/test_newsradar.py tests/test_trendradar.py tests/test_trendradar_refresh.py tests/test_graph_end_to_end.py
```

Tests cover public payload normalization, domain validation, timezone handling,
RSS/Atom/JSON Feed, partial failure, URL/GUID deduplication, off-list/re-entry
history, source overrides, subprocess collection without a checkout, snapshot
compatibility, timeout/cooldown, file locks and original-analysis recovery.

# TrendRadar local news enrichment (P0 + P1)

TradingAgents reads a separate TrendRadar checkout's SQLite files, recalls news
with deterministic company aliases and optional industry/macro terms, and freezes
the selected evidence before the News Analyst runs. Existing Yahoo/Alpha Vantage,
FRED and prediction-market tools continue to work. No AI classification is used.
Before freezing a new current-date context, TradingAgents reuses fresh source
data or runs a bounded collector subprocess. Collection failures are optional
evidence, never a replacement for the original news tools.

## Local setup

Use [Nana0919B/TrendRadar](https://github.com/Nana0919B/TrendRadar), branch
`master`, for collection. This fork includes the lightweight collector imports,
working Hacker News / Yahoo AAPL feeds, and the RSS publication-time timezone
fix. Use commit `a7cafdb177adc966d2555febedd53ee582db713d` or a descendant;
an unrelated upstream checkout does not guarantee these fixes.

From the TradingAgents repository root, clone the fork into a sibling folder
and install the collection dependencies in TradingAgents' virtual environment
with `uv`:

```sh
git clone --branch master https://github.com/Nana0919B/TrendRadar.git ../TrendRadar
uv pip install --python .venv/bin/python -e '.[trendradar]'
git -C ../TrendRadar merge-base --is-ancestor a7cafdb177adc966d2555febedd53ee582db713d HEAD
```

For a pip-enabled environment, use `.venv/bin/python -m pip install -e '.[trendradar]'`
instead of the `uv pip install` command.

The last command must exit successfully (status 0). Find the checkout's absolute
path with `git -C ../TrendRadar rev-parse --show-toplevel`, then substitute it
for `/absolute/path/to/TrendRadar` below. Create `.env` from `.env.example` if
needed and enable these settings (loaded by CLI and the local web server):

```dotenv
TRADINGAGENTS_TRENDRADAR_ENABLED=true
TRADINGAGENTS_TRENDRADAR_DATA_DIR=/absolute/path/to/TrendRadar/output
TRADINGAGENTS_TRENDRADAR_PROJECT_DIR=/absolute/path/to/TrendRadar
TRADINGAGENTS_TRENDRADAR_TIMEZONE=Asia/Shanghai
TRADINGAGENTS_TRENDRADAR_ARTICLE_LIMIT=30
TRADINGAGENTS_TRENDRADAR_AUTO_REFRESH=true
TRADINGAGENTS_TRENDRADAR_REFRESH_MINUTES=30
TRADINGAGENTS_TRENDRADAR_REFRESH_TIMEOUT=60
```

The package default is disabled. Restart an already-running web server to read
the new environment. Reading does not require installing TrendRadar or its AI
runtime in TradingAgents. Missing, stale, corrupt or incompatible source
databases produce explicit unavailable/partial coverage; they do not replace or
block the original news tools.

TrendRadar's source and local databases are **not bundled** with TradingAgents.
Developers cloning only TradingAgents can run the original analysis normally;
the optional enrichment is disabled by default. To enable collection, prepare a
separate checkout of the fork above and configure its path on that machine. There are
no hard-coded personal paths in the runtime. Missing optional source, interpreter
or dependencies is reported as refresh unavailable/failed, without blocking the
original analysis. Existing readable fresh databases can be reused even if the
collector checkout is absent.

The fork's lazy AppContext imports allow collection with the lightweight
`.[trendradar]` extra in TradingAgents' existing environment. The collector
loads the configured checkout directly; installing TrendRadar's full AI runtime
is unnecessary. Leave `TRADINGAGENTS_TRENDRADAR_PYTHON` unset to use the same
Python as TradingAgents, or set it to an absolute interpreter path with the
collection dependencies installed. Collection does not call an AI model.

For an existing checkout, switch its `origin` to the fork and update it:

```sh
git -C /absolute/path/to/TrendRadar remote set-url origin https://github.com/Nana0919B/TrendRadar.git
git -C /absolute/path/to/TrendRadar pull --ff-only origin master
git -C /absolute/path/to/TrendRadar merge-base --is-ancestor a7cafdb177adc966d2555febedd53ee582db713d HEAD
```

The configured interpreter loads the updated source on the next collection.
Restart TradingAgents after changing `.env`. Run the manual collection command
below to verify feeds; new research uses the new evidence, while existing jobs
retain their frozen inputs. The fork's Yahoo RSS feed is AAPL-specific; configure
additional company feeds in its `config/config.yaml` when researching other stocks.

## Automatic refresh

- Enabled enrichment automatically checks freshness before **new** current-date
  research in both the CLI and Web flows. `AUTO_REFRESH=false` keeps read-only
  operation.
- Successful hotlist and RSS observations within 30 minutes are reused. A source
  failure or touched database file alone is not evidence of freshness. Recent
  observations just before midnight also count.
- Otherwise a subprocess collects the configured financial platforms
  (`wallstreetcn-hot`, `cls-hot`, `zhihu` by default) and enabled RSS feeds.
  Programmatic `trendradar_refresh_platforms=[]` selects all configured platforms.
- Default timeout: 60 seconds. Collection is killed on timeout. A nonblocking
  file lock shared by runs using the same data directory prevents concurrent
  collectors; another run continues with available data rather than waiting.
- Failed/timed-out attempts have a 5-minute cooldown. A successful partial
  collection is reused for 30 minutes to avoid repeatedly retrying broken feeds.
  Missing data after a successful attempt is checked again rather than trusted
  solely from the previous refresh marker.
- The snapshot cutoff is taken **after** refresh, so newly collected items are
  eligible. Failure still permits cached data within the existing 24-hour
  fallback budget; otherwise evidence is unavailable and normal tools continue.
- Frozen inputs, Web retries, checkpoint resumes and historical research do not
  collect again. Reports expose the refresh outcome alongside coverage.

Refresh state and a bounded-per-attempt collector log are kept under
`output/.tradingagents-refresh/`. No cron or standing service is required.

`refreshed` means at least one source supplied items; it does not mean every
feed succeeded. Reports list collected totals before company keyword filtering
and failed sources for each collection type. Coverage failures aggregate the
lookback window, so a past failure can remain visible after a source recovers.
Existing reports keep their frozen evidence; start a new research job to use
newly collected news (retrying a job reuses its original evidence).

If RSS fails, inspect `collector.log` for the underlying HTTP/TLS error. Edit
`config/config.yaml` in the configured TrendRadar checkout, not TradingAgents.
For example, Hacker News provides `https://news.ycombinator.com/rss`; Yahoo's
AAPL feed is `https://finance.yahoo.com/rss/headline?s=AAPL` (specific to AAPL,
not market-wide coverage). A 404 requires checking the feed URL; network/TLS
errors and HTTP 429 require checking connectivity or waiting for rate limits.
Re-run the explicit collector command below to verify the configured feeds.

## Collect data once

From the TradingAgents repository root, collect once using the fork:

```sh
uv pip install --python .venv/bin/python -e '.[trendradar]'
.venv/bin/python -m tradingagents.dataflows.trendradar_collect \
  --project /absolute/path/to/TrendRadar \
  --platforms wallstreetcn-hot cls-hot zhihu
```

Omit `--platforms` to collect all configured platforms. Enabled RSS feeds are
also collected. This command never invokes AI, notifications, reports, or
retention cleanup. It uses the checkout's source configuration and writes to
its `output` directory (or explicit `--output-dir`). Use `--timezone` to match
the configured source timezone. The old `scripts/collect_trendradar.py` entry
point remains a compatibility wrapper. Check stdout for each RSS source's
item count and failure details. An overall successful exit can still represent
partial collection; inspect both the hotlist and RSS results.

## Rules and limits

Programmatic configuration can add or override per-ticker recall terms:

```python
config.update(
    trendradar_enabled=True,
    trendradar_data_dir="/path/to/TrendRadar/output",
    trendradar_lookback_days=7,
    trendradar_max_age_hours=24,
    trendradar_article_limit=30,
    trendradar_terms={
        "NVDA": {
            "direct": ["英伟达", "NVIDIA"],
            "industry": ["算力", "数据中心", "先进封装"],
            "macro": ["出口管制"],
        }
    },
)
```

Built-in aliases cover several common US/HK companies. Resolved company names
are also used. Industry/macro expansion is explicit, not inferred by a model.
Rules match titles, use boundaries for ASCII words, and prioritize direct over
industry over macro matches, then newest observations. URLs are deduplicated;
other platforms observing that URL are retained in `other_sources`. The same
event under different URLs is not semantically clustered in this phase.

At most 31 daily partitions per type, 10,000 rows per partition, 100 output
articles (default 30), and 24 rank observations per article are retained. Each
database is opened with SQLite `mode=ro`, query-only mode and a read transaction.
There is no atomic transaction across different daily databases; the snapshot
records observed collection times and partial coverage instead.

## Time and evidence guarantees

- The cutoff is the news context's preparation time, after optional refresh. A
  hotlist's first observation is not a publication date; reports label unknown
  publication dates explicitly.
- Future rows, publication times and rank observations are excluded. Rows
  revised after the cutoff are omitted because the prior content cannot safely
  be reconstructed. TrendRadar's naive revision timestamps use its configured
  local timezone.
- Fresh extraction from mutable databases is allowed only for the current local
  date. Historical requests return unavailable rather than reinterpret today's
  database rows as point-in-time evidence. A previously frozen context may be
  supplied for its original ticker/date, including on a later day.
- A successful source observation within `trendradar_max_age_hours` is required;
  failed observations do not count as fresh coverage. Even successful coverage
  is partial: a hotlist is a sample, not proof of an absence of news.
- Cross-platform ranks measure attention, not sentiment, truth or expected
  returns. Summaries are feed data, not verified article bodies. No body fetch
  or AI filtering takes place.

## Archive, recovery and display

`news_context` contains its schema version, ticker/date, timezone-aware cutoff,
rules, coverage and source-linked items. `snapshot_id` is SHA-256 over the full
payload. Evidence IDs include day/type/platform/item ID, avoiding collisions
between per-day databases.

The graph archives contexts in `data_cache_dir/news.sqlite3`; web jobs also
archive them in their research store and persist them on the job **before** any
analyst runs. Job retries reuse those inputs. Graph checkpoint resumes use the
checkpointed context without re-reading TrendRadar. An explicitly supplied
context is validated and included in the run signature. Changed or mismatched
evidence is rejected rather than silently fetched again.

For programmatic replay:

```python
from tradingagents.dataflows.research_store import ResearchStore

news = ResearchStore("/path/to/cache/news.sqlite3").load_news_context(snapshot_id)
graph = TradingAgentsGraph(config=config, news_context=news)
state, rating = graph.propagate(news["ticker"], news["trade_date"])
```

This freezes only the TrendRadar supplement. Existing company-news tools and
other data paths retain their own historical limitations; replay is not a
promise of identical model output or fully frozen external tool results.

The News Analyst receives the context as labelled evidence and appends a
deterministic source/coverage appendix even if the model omits references.
The original `news_report` flows into downstream research and risk analysis.
Full state logs and report headers contain the snapshot identity. Web news
reports show the evidence appendix alongside the existing report.

## Verification

```sh
.venv/bin/python -m pytest tests/test_trendradar.py tests/test_trendradar_refresh.py tests/test_graph_end_to_end.py \
  tests/test_news_recovery.py tests/test_news_lookahead.py tests/test_news_analyst_prompt.py
```

Tests use real temporary SQLite files and scripted models, without external AI
or source requests. Coverage includes source failures, corrupt/missing files,
date boundaries, title revisions, alias matching, RSS, immutable archives,
prompt injection of evidence, citations, web retry and graph checkpoint recovery.
Refresh tests cover reuse, expiry, missing optional source, partial collection,
timeout, nonzero exit, cooldown, real file-lock contention, midnight and original
news tools continuing through a collector timeout.

"""Loopback-only UI. Run with python -m tradingagents.web.server."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt

from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.market_environment import (
    build_market_environment,
    validate_market_environment,
)
from tradingagents.dataflows.research_analytics import enrich_snapshot
from tradingagents.dataflows.research_store import ResearchStore
from tradingagents.dataflows.symbols import resolve_equity_symbol
from tradingagents.dataflows.vendors.sec_edgar import get_filing_documents
from tradingagents.dataflows.web_data import company_data, peer_data
from tradingagents.default_config import build_default_config
from tradingagents.report_brief import make_company_brief
from tradingagents.report_html import render_report
from tradingagents.reporting import _report_parts, write_report_tree
from tradingagents.web import backtest_api
from tradingagents.web.bootstrap import initialize_shared_data
from tradingagents.web.presentation import present_report, readable_evidence

BACKTESTS_PAUSED = True  # Keep archives readable; pause new strategy simulations.
ROOT = Path(__file__).parent / "static"
STORE = Path(build_default_config()["results_dir"]) / "web"
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
VALUATION_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="valuation")
LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")
log = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def symbol(value, market="auto"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("请输入股票代码")
    return resolve_equity_symbol(value, market)


def persist(job):
    STORE.mkdir(parents=True, exist_ok=True)
    replace_file(
        STORE / f"{job['id']}.json",
        lambda p: Path(p).write_text(
            json.dumps(job, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        ),
    )


def set_library_state(job_id, state):
    if state not in ("active", "archived", "trash"):
        raise ValueError("资料状态无效")
    with LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("研究不存在")
        if (job.get("status") in ("running", "queued") or job.get("data_status") == "loading"
                or job.get("automatic_valuation", {}).get("status") in ("running", "queued")):
            raise ValueError("研究正在运行，请完成后再整理")
        changed = {**job, "library_state": state, "library_updated_at": datetime.now().astimezone().isoformat()}
        persist(changed)
        JOBS[job_id] = changed
    return {"id": job_id, "library_state": state}


def update(job_id, **changes):
    with LOCK:
        JOBS[job_id].update(changes)
        persist(JOBS[job_id])


def display_reports(job):
    reports = dict(job.get("reports", {}))
    macro = job.get("macro_update") if not job.get("macro_consumed") else None
    if macro and macro.get("status") == "completed":
        original = reports.get("news_report", "")
        original = "\n".join(
            line
            for line in original.splitlines()
            if not (
                "FRED" in line
                and ("未配置" in line or ("get_macro_indicators" in line and "不可用" in line))
            )
        )
        reports["news_report"] = (
            "## 宏观数据已补充\n"
            + f"补充时间：{macro['retrieved_at']}。以下为 FRED 官方公开 CSV 最新修订值，"
            "不是原报告时点的历史版本；原有新闻事件分析未重新生成。\n\n"
            + "\n\n".join(macro["reports"])
            + "\n\n---\n\n"
            + original
        )
    snapshot = job.get("research_snapshot")
    if snapshot and reports.get("operating_report") and reports.get("metrics_report"):
        reports["company_brief"] = make_company_brief(snapshot, reports)
    return {key: present_report(text, snapshot) for key, text in reports.items()}


class PauseRequested(Exception):
    """Cooperative pause at a durable boundary, not a provider error."""


def check_pause(job_id):
    with LOCK:
        if JOBS[job_id].get("pause_requested"):
            raise PauseRequested()


def submit_work(function, job_id):
    with LOCK:
        token = uuid.uuid4().hex
        JOBS[job_id]["work_token"] = token
        persist(JOBS[job_id])
    executor = VALUATION_POOL if function is run_automatic_valuation else POOL
    return executor.submit(run_scheduled, function, job_id, token)


def run_scheduled(function, job_id, token):
    with LOCK:
        if JOBS[job_id].get("work_token") != token:
            return
        if function is run_job:
            JOBS[job_id]["status"] = "running"
        else:
            JOBS[job_id]["automatic_valuation"]["status"] = "running"
        persist(JOBS[job_id])
    function(job_id)


def pause_job(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ValueError("研究不存在")
        a = job.get("automatic_valuation", {})
        if job.get("status") == "paused" or a.get("status") == "paused":
            return {"id": job_id, "status": "paused"}
        active = job.get("status") in ("queued", "running") or a.get("status") in ("queued", "running")
        if not active:
            raise ValueError("当前没有运行中的研究或估值")
        running = job.get("status") == "running" or a.get("status") == "running"
        job["pause_requested"] = True
        if running:
            if job.get("status") == "completed":
                job["automatic_valuation"]["phase"] = "正在暂停 · 当前模型调用结束后保存进度"
            else:
                job["phase"] = "正在暂停 · 当前模型调用或并行批次结束后保存进度"
        else:
            job["work_token"] = uuid.uuid4().hex  # invalidate the queued worker
            if job.get("status") == "queued":
                job["status"] = "paused"
            else:
                job["automatic_valuation"] = {**a, "status": "paused", "phase": "估值已暂停"}
            if job.get("status") == "paused":
                job["phase"] = "已暂停，资料与已完成报告保留"
        persist(job)
    return {"id": job_id, "status": "pausing" if running else "paused"}


def resume_job(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ValueError("研究不存在")
        research_paused = job.get("status") == "paused"
        valuation_paused = job.get("automatic_valuation", {}).get("status") == "paused"
        if not research_paused and not valuation_paused:
            raise ValueError("请等待任务暂停完成后再继续")
    if research_paused:
        return retry_job(job_id)
    return queue_automatic_valuation(job_id)


def queue_research(ticker, peers, day):
    from datetime import date

    from tradingagents.dataflows.point_in_time import VERSION
    try:
        if date.fromisoformat(day).isoformat() != day or day > today():
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("请选择不晚于今天的有效日期") from None
    historical = day < today()
    if historical and ticker.endswith((".HK", ".SS", ".SZ")):
        raise ValueError("该市场完整历史原始披露尚未接入，暂不能生成同流程的历史公司估值；不会回退行业默认参数")
    with LOCK:
        if historical:
            for previous in JOBS.values():
                if (previous.get("ticker") == ticker and previous.get("date") == day
                        and previous.get("research_pipeline") == VERSION
                        and previous.get("library_state", "active") == "active"
                        and previous.get("status") in ("queued", "running", "completed")):
                    return {"id": previous["id"], "status": previous["status"], "reused": True}
        if sum(j["status"] in ("running", "queued") for j in JOBS.values()) >= 3:
            raise ValueError("已有 3 个任务，请等待完成")
        job = {"id": uuid.uuid4().hex, "ticker": ticker, "peers": [] if historical else peers,
               "date": day, "created_at": datetime.now().astimezone().isoformat(),
               "status": "queued", "phase": "等待公司研究与自动估值",
               "automatic_valuation": {"status": "pending", "phase": "研究完成后自动核实并生成公司估值"}}
        if historical:
            job.update(research_mode="point_in_time", research_pipeline=VERSION)
        JOBS[job["id"]] = job
        persist(job)
    submit_work(run_job, job["id"])
    return {"id": job["id"], "status": "queued"}


def run_job(job_id):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    try:
        check_pause(job_id)
        job = JOBS[job_id]
        update(job_id, status="running", phase="正在获取公司资料与财务报表")
        if job.get("research_mode") == "point_in_time" and not job.get("research_snapshot"):
            from tradingagents.web.point_in_time import build_snapshot
            folder = STORE / "historical-research" / job_id
            update(job_id, phase="正在冻结观察日前原始财报、季度数据与收盘价")
            snapshot = build_snapshot(job, folder)
            archive = ResearchStore(STORE / "research.sqlite3")
            digest = archive.archive_snapshot(snapshot)
            update(job_id, research_snapshot=snapshot, data=snapshot["company"],
                   snapshot_digest=digest, pit_source_folder=str(folder))
            job = JOBS[job_id]
        if job.get("research_snapshot") is not None:
            snapshot = job["research_snapshot"]
            archive = ResearchStore(STORE / "research.sqlite3")
            phase = (
                "复用财务数据与已完成的四份分析，恢复综合判断"
                if job.get("seed_reports", {}).get("news_report")
                else "复用已归档财务数据，重新生成研究与新闻分析"
            )
            update(job_id, error=None, phase=phase)
        else:
            data = company_data(job["ticker"])
            update(job_id, data=data, phase="正在获取同行估值与财务指标")
            peers = [peer_data(peer) for peer in job["peers"]]
            try:
                data["filings"] = get_filing_documents(job["ticker"], job["date"])
            except Exception as exc:
                data["filings"] = {
                    "documents": [],
                    "note": "原始披露索引暂不可用：" + type(exc).__name__,
                }
            snapshot = {
                "id": job_id,
                "schema_version": 2,
                "as_of_date": job["date"],
                "company": data,
                "peers": peers,
            }
            enrich_snapshot(snapshot)
            archive = ResearchStore(STORE / "research.sqlite3")
            changes = archive.profile_changes(snapshot)
            digest = archive.archive_snapshot(snapshot)
            archive.save_peers(job["ticker"], job["peers"])
            update(
                job_id, research_snapshot=snapshot, snapshot_digest=digest, profile_changes=changes
            )
            update(
                job_id,
                peers_data=peers,
                phase="基本面、经营、财务指标与新闻智能体并行研究；随后进行多空辩论与风险评估",
            )
        check_pause(job_id)
        config = build_default_config()
        # Isolate mutable graph, memory and checkpoints from CLI/other runs.
        config.update(
            results_dir=str(STORE / "engine" / job_id),
            data_cache_dir=str(STORE / "cache"),
            memory_log_path=str(STORE / "memory.md"),
            checkpoint_enabled=False,
            llm_timeout_seconds=120,
            # SSE starts the response before a long report finishes, avoiding
            # proxy disconnects while waiting for non-streaming headers.
            anthropic_streaming=True,
            llm_fallback_to_deep=True,
        )

        if job.get("research_mode") == "point_in_time":
            from tradingagents.web.point_in_time import NOTICE, validate_snapshot
            validate_snapshot(snapshot, job["ticker"], job["date"])
            config.update(trendradar_enabled=False, market_environment_enabled=False,
                          memory_log_path=str(STORE / "engine" / job_id / "memory.md"))
            # No unversioned web news. This slot discloses coverage, it does not
            # pretend that the historical news analyst retrieved full coverage.
            job = {**job, "seed_reports": {**job.get("seed_reports", {}), "news_report": NOTICE + " 历史新闻档案未接入；仅使用财报披露事件。"}}
            update(job_id, phase="正在基于历史披露生成基本面、经营及财务指标研究")

        if job.get("frozen_resume"):
            config.update(frozen_resume=True, trendradar_enabled=False)

        def on_report(key, text):
            with LOCK:
                JOBS[job_id].setdefault("reports", {})[key] = text
                persist(JOBS[job_id])

        graph_options = {}
        environment = job.get("market_environment")
        if environment is None:
            environment = build_market_environment(
                config, job["ticker"], job["date"],
                allow_fetch=not bool(job.get("seed_reports")) and not job.get("retried_at"),
            )
        if environment is not None:
            validate_market_environment(environment, job["ticker"], job["date"])
            archive.archive_market_environment(environment)
            update(job_id, market_environment=environment, market_environment_consumed=False)
            graph_options["market_environment"] = environment
        from tradingagents.dataflows.trendradar_adapter import (
            validate_news_context,
        )
        from tradingagents.dataflows.trendradar_refresh import prepare_news_context

        news = job.get("news_context")
        if news is not None:
            validate_news_context(news, job["ticker"], job["date"])
        elif config.get("trendradar_enabled") and not job.get("seed_reports", {}).get("news_report"):
            news = prepare_news_context(config, job["ticker"], job["date"], snapshot["company"].get("profile", {}))
        if news is not None:
            archive.archive_news_context(news)
            update(job_id, news_context=news)
            graph_options["news_context"] = news
        if job.get("macro_context") is not None:
            graph_options["macro_context"] = job["macro_context"]
        if job.get("seed_reports"):
            graph_options["seed_reports"] = job["seed_reports"]
        graph = TradingAgentsGraph(
            selected_analysts=["fundamentals", "operating", "metrics", "news"],
            config=config,
            research_snapshot=snapshot,
            on_report=on_report,
            **graph_options,
        )
        graph.pause_check = lambda: check_pause(job_id)
        check_pause(job_id)
        state, rating = graph.propagate(job["ticker"], job["date"])
        reports = {
            k: state.get(k, "")
            for k in (
                "fundamentals_report",
                "operating_report",
                "metrics_report",
                "news_report",
                "investment_plan",
                "trader_investment_plan",
                "final_trade_decision",
            )
        }
        reports["company_brief"] = make_company_brief(snapshot, reports)
        write_report_tree(
            {**state, **{key: present_report(value, snapshot) for key, value in reports.items()}},
            job["ticker"],
            STORE / "exports" / job_id,
            settings=graph.run_settings(),
        )
        export_file = STORE / "exports" / job_id / "complete_report.html"
        if export_file.exists():
            export_file.write_text(
                present_report(export_file.read_text(), snapshot), encoding="utf-8"
            )
        if job.get("macro_context") is not None:
            archive.archive_report_version(
                job_id,
                snapshot,
                job["macro_context"],
                reports,
                job["parent_job_id"],
                job["created_at"],
            )
        else:
            archive.archive_reports(snapshot, reports)
        update(
            job_id,
            status="completed",
            phase="分析完成",
            reports=reports,
            rating=rating,
            macro_consumed=bool(job.get("macro_context")),
            market_environment_consumed=bool(
                environment and state.get("market_environment", {}).get("snapshot_id") == environment["snapshot_id"]
            ),
        )
        # A failed valuation enqueue must not relabel completed research as failed.
        try:
            queue_automatic_valuation(job_id)
        except Exception:
            log.exception("Could not enqueue valuation for %s", job_id)
            update(job_id, automatic_valuation={"status": "failed", "gaps": ["估值任务未能入队，研究报告已保存，可单独重试估值"]})
    except PauseRequested:
        update(job_id, status="paused", phase="研究已暂停，已完成报告保留；继续时重做未完成的汇总步骤")
    except Exception as exc:
        log.exception("Research job %s failed", job_id)
        # Do not expose SDK errors containing headers, keys or internal URLs.
        update(
            job_id,
            status="failed",
            phase="分析未完成，可查看已获取的数据",
            error=f"{type(exc).__name__}：请检查模型服务、网络及终端日志后重试。",
            **({"automatic_valuation": {"status": "blocked", "gaps": ["公司研究尚未完成，未使用行业默认参数替代"]}}
               if JOBS[job_id].get("automatic_valuation", {}).get("status") == "pending" else {}),
        )


def valuation_service():
    from tradingagents.web.valuation_service import ValuationService
    return ValuationService(store=STORE, jobs=JOBS, lock=LOCK, persist=persist,
                            update=update, check_pause=check_pause, pause_exception=PauseRequested)


def save_valuation(job_id, result):
    return valuation_service().save(job_id, result)


def run_automatic_valuation(job_id):
    return valuation_service().run(job_id)


def queue_automatic_valuation(job_id):
    return valuation_service().queue(job_id, lambda jid: submit_work(run_automatic_valuation, jid))


def retry_job(job_id):
    """Finish a failed run using its archived evidence, including after midnight."""
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") not in ("failed", "paused"):
            raise ValueError("只可继续暂停或失败的研究")
        seeds = {
            key: job.get("reports", {}).get(key, "")
            for key in ("fundamentals_report", "operating_report", "metrics_report", "news_report")
        }
        snapshot = job.get("research_snapshot")
        if job.get("market_environment") is not None:
            validate_market_environment(job["market_environment"], job["ticker"], job["date"])
        pit = job.get("research_mode") == "point_in_time"
        complete_analysts = all(isinstance(v, str) and v.strip() for v in seeds.values())
        if job.get("date") != today() and not pit and not (snapshot and (complete_analysts or job["status"] == "paused")):
            raise ValueError("历史失败任务缺少完整分析报告，请新建研究")
        if snapshot:
            if snapshot.get("as_of_date") != job["date"] or snapshot["company"]["symbol"] != job["ticker"]:
                raise ValueError("财务归档日期或股票代码不匹配")
            digest = hashlib.sha256(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if digest != job.get("snapshot_digest"):
                raise ValueError("财务归档校验失败，不能重用")
        if sum(j["status"] in ("running", "queued") for j in JOBS.values()) >= 3:
            raise ValueError("已有三个任务，请等待完成")
        if snapshot:
            job["seed_reports"] = {k: v for k, v in seeds.items() if isinstance(v, str) and v.strip()}
        if snapshot and job.get("date") != today() and not pit:
            job["seed_reports"].setdefault("news_report", "原任务暂停或失败前未完成新闻归档；跨日继续不补取今日新闻，新闻覆盖不足。")
            job["frozen_resume"] = True
        job.update(
            status="queued", phase="等待恢复：复用已完成的分析与财务数据", error=None, pause_requested=False,
            retried_at=datetime.now().astimezone().isoformat(),
        )
        persist(job)
    submit_work(run_job, job_id)
    return job


def create_macro_reanalysis(source_id):
    from tradingagents.agents.macro_context import validate_macro_context

    with LOCK:
        source = JOBS.get(source_id)
        if not source or source.get("status") != "completed":
            raise ValueError("请选择已完成的研究")
        snapshot = source.get("research_snapshot")
        macro = source.get("macro_update") or source.get("macro_context")
        if not snapshot or snapshot.get("as_of_date") != today():
            raise ValueError("只支持复用今日财务资料；历史日期请新建研究")
        validate_macro_context(macro, today())
        digest = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if digest != source.get("snapshot_digest"):
            raise ValueError("财务归档校验失败，不能重用")
        seeds = {
            key: source.get("reports", {}).get(key, "")
            for key in ("fundamentals_report", "operating_report", "metrics_report")
        }
        if not all(seeds.values()):
            raise ValueError("缺少可复用的财务分析报告")
        if sum(job["status"] in ("running", "queued") for job in JOBS.values()) >= 3:
            raise ValueError("已有三个任务，请等待完成")
        job = {
            "id": uuid.uuid4().hex,
            "ticker": source["ticker"],
            "date": today(),
            "peers": list(source.get("peers", [])),
            "created_at": datetime.now().astimezone().isoformat(),
            "status": "queued",
            "phase": "等待使用 FRED 数据重新生成新闻与综合判断",
            "parent_job_id": source_id,
            "research_snapshot": deepcopy(snapshot),
            "snapshot_digest": digest,
            "macro_context": deepcopy(macro),
            "seed_reports": seeds,
            "macro_consumed": False,
        }
        if source.get("market_environment") is not None:
            validate_market_environment(source["market_environment"], source["ticker"], job["date"])
            job["market_environment"] = deepcopy(source["market_environment"])
        if source.get("news_context") is not None:
            job["news_context"] = deepcopy(source["news_context"])
        JOBS[job["id"]] = job
        persist(job)
    submit_work(run_job, job["id"])
    return job


def load_history():
    if STORE.exists():
        for path in STORE.glob("*.json"):
            try:
                job = json.loads(path.read_text())
                if job["status"] in ("queued", "running"):
                    job.update(
                        status="failed", phase="服务已重启，请重新发起分析", error="上次任务被中断"
                    )
                if job.get("automatic_valuation", {}).get("status") in ("running", "queued"):
                    job["automatic_valuation"] = {"status": "failed", "gaps": ["服务重启中断自动估值，请重试；已有估值保留"]}
                    persist(job)
                JOBS[job["id"]] = job
            except (ValueError, KeyError):
                continue
    # Import existing CLI reports once, without re-running paid analysis.
    base = Path(build_default_config()["results_dir"])
    for path in base.glob("reports/*/complete_report.md"):
        key = uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex
        if key in JOBS:
            continue
        text = path.read_text()
        match = re.search(r"Analysis date: (\d{4}-\d{2}-\d{2})", text)
        ticker = path.parent.name.rsplit("_", 2)[0]
        files = {
            "fundamentals_report": "1_analysts/fundamentals.md",
            "operating_report": "1_analysts/operating.md",
            "metrics_report": "1_analysts/metrics.md",
            "news_report": "1_analysts/news.md",
            "investment_plan": "2_research/manager.md",
            "trader_investment_plan": "3_trading/trader.md",
            "final_trade_decision": "5_portfolio/decision.md",
        }
        reports = {
            k: (path.parent / v).read_text() if (path.parent / v).exists() else ""
            for k, v in files.items()
        }
        job = {
            "id": key,
            "ticker": ticker,
            "date": match[1] if match else today(),
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
            "status": "completed",
            "phase": "已导入本地研究报告",
            "reports": reports,
            "peers": [],
            "imported": True,
        }
        JOBS[key] = job
        persist(job)


class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, content_type="application/json; charset=utf-8"):
        body = (
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            if content_type.startswith("application/json")
            else data
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        return self.headers.get("Host") in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def do_GET(self):
        if not self.trusted():
            return self.send({"error": "不支持的主机"}, 403)
        path = urlparse(self.path).path
        if path == "/api/jobs":
            scope = parse_qs(urlparse(self.path).query).get("scope", ["active"])[0]
            if scope not in ("active", "archived", "trash"):
                return self.send({"error": "资料范围无效"}, 400)
            with LOCK:
                jobs = [
                    {
                        **{
                            k: v
                            for k, v in job.items()
                            if k not in (
                                "data", "reports", "peers_data", "research_snapshot", "valuation", "historical_context", "automatic_valuation",
                                "news_context", "seed_reports", "macro_context", "macro_update", "market_environment", "market_environment_update",
                            )
                        },
                        "automatic_valuation": {k: job.get("automatic_valuation", {}).get(k) for k in ("status", "phase", "run_id")},
                        "pause_requested": job.get("pause_requested", False),
                        "company_name": (
                            (job.get("research_snapshot") or {}).get("company", {}).get("profile", {})
                            or (job.get("data") or {}).get("profile", {})
                        ).get("longName", job.get("ticker", "")),
                    }
                    for job in JOBS.values()
                    if job.get("library_state", "active") == scope
                ]
            return self.send(
                {
                    "jobs": sorted(jobs, key=lambda j: j["created_at"], reverse=True),
                    "today": today(),
                }
            )
        if path.startswith("/api/peers/"):
            try:
                ticker = symbol(
                    path.rsplit("/", 1)[-1],
                    parse_qs(urlparse(self.path).query).get("market", ["auto"])[0],
                )
                return self.send(
                    {
                        "ticker": ticker,
                        "peers": ResearchStore(STORE / "research.sqlite3").peer_pool(ticker),
                    }
                )
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if path.startswith("/api/jobs/") and path.endswith("/export"):
            job_id = path.split("/")[-2]
            if job_id not in JOBS:
                return self.send({"error": "任务不存在"}, 404)
            file = STORE / "exports" / job_id / "complete_report.html"
            job = JOBS[job_id]
            if not file.exists() and not (job.get("status") == "completed" and job.get("reports")):
                return self.send({"error": "该任务尚无完整导出文件"}, 404)
            display = display_reports(JOBS[job_id])
            state = {**display, "trade_date": JOBS[job_id]["date"],
                     "market_environment": job.get("market_environment_update") or job.get("market_environment")}
            html = render_report(JOBS[job_id]["ticker"], state, None, _report_parts(state))
            return self.send(
                present_report(html, JOBS[job_id].get("research_snapshot")).encode(),
                content_type="text/html; charset=utf-8",
            )

        if path.startswith("/api/jobs/"):
            with LOCK:
                job = JOBS.get(path.rsplit("/", 1)[-1])
                result = json.loads(json.dumps(job)) if job else None
            if result is None:
                return self.send({"error": "任务不存在"}, 404)
            if result.get("research_snapshot") is not None:
                result["data"] = result["research_snapshot"]["company"]
                result["peers_data"] = result["research_snapshot"]["peers"]
            ticker = result.get("ticker") or result.get("research_snapshot", {}).get(
                "company", {}
            ).get("symbol")
            archive = ResearchStore(STORE / "research.sqlite3")
            result["valuation_setup"] = valuation_service().prepare(result)
            result["valuation_reference_quote"] = valuation_service().reference_quote(result)
            result["versions"] = archive.history(ticker) if ticker else []
            result["display_evidence"] = readable_evidence(result.get("research_snapshot") or {})
            result["report_html"] = {
                k: MARKDOWN.render(v) for k, v in display_reports(result).items()
            }
            return self.send(result)
        if path == "/api/valuation-backtests":
            return self.send(backtest_api.records(STORE))
        if path.startswith("/api/valuation-backtests/"):
            try:
                return self.send(backtest_api.read(STORE, path.rsplit("/", 1)[-1]))
            except ValueError as exc:
                return self.send({"error": str(exc)}, 404)
        files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                 "/valuation.js": "valuation.js", "/valuation.css": "valuation.css",
                 "/backtest": "backtest.html", "/backtest.js": "backtest.js", "/backtest.css": "backtest.css"}
        if path not in files:
            return self.send({"error": "页面不存在"}, 404)
        file = ROOT / files[path]
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }
        self.send(file.read_bytes(), content_type=types[file.suffix])

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        if not self.trusted() or (origin and origin not in allowed):
            return self.send({"error": "请求来源不允许"}, 403)
        if self.path.startswith("/api/jobs/") and self.path.endswith("/library"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not self.headers.get("Content-Type", "").startswith("application/json") or not 0 < length <= 1024:
                    raise ValueError("请求必须为有效 JSON")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求必须为对象")
                return self.send(set_library_state(self.path.split("/")[-2], payload.get("state")))
            except (ValueError, TypeError) as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/valuation-backtests/") and self.path.endswith("/research"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not self.headers.get("Content-Type", "").startswith("application/json") or not 0 < length <= 4096:
                    raise ValueError("请求必须为有效 JSON")
                payload = json.loads(self.rfile.read(length))
                record = backtest_api.research_record(STORE, self.path.split("/")[-2], payload.get("ticker"), payload.get("date"))
                with LOCK:
                    if record["id"] not in JOBS:
                        JOBS[record["id"]] = record
                        persist(record)
                return self.send({"id": record["id"], "url": "/?job=" + record["id"] + "#valuation"}, 201)
            except (ValueError, TypeError, AttributeError) as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path == "/api/valuation-backtests":
            if BACKTESTS_PAUSED:
                return self.send({"error": "策略回测暂时停用，已有记录仍可查看；请先使用公司研究与多日期估值对比"}, 409)
            try:
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("请求必须为 JSON")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 4096:
                    raise ValueError("请求大小无效")
                row = backtest_api.create(STORE, json.loads(self.rfile.read(length)))
                return self.send({"id": row["id"], "status": row["status"]}, 202)
            except (ValueError, TypeError) as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/jobs/") and self.path.rsplit("/", 1)[-1] in ("pause", "resume"):
            try:
                action = pause_job if self.path.endswith("/pause") else resume_job
                result = action(self.path.split("/")[-2])
                return self.send({"id": result["id"], "status": result["status"]}, 202)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/jobs/") and self.path.endswith("/automatic-valuation"):
            try:
                return self.send(queue_automatic_valuation(self.path.split("/")[-2]), 202)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/jobs/") and self.path.endswith("/valuation"):
            job_id = self.path.split("/")[-2]
            with LOCK:
                job = deepcopy(JOBS.get(job_id))
            if job is None:
                return self.send({"error": "任务不存在"}, 404)
            try:
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("请求必须为 JSON")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 131072:
                    raise ValueError("请求大小无效")
                payload = json.loads(self.rfile.read(length))
                result = valuation_service().calculate(job, payload)
                save_valuation(job_id, result)
                update(job_id, valuation=result)
                return self.send(result)
            except (ValueError, TypeError, OverflowError) as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/jobs/") and self.path.endswith("/reanalyze-macro"):
            try:
                job = create_macro_reanalysis(self.path.split("/")[-2])
                return self.send({"id": job["id"], "status": "queued"}, 202)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path.startswith("/api/jobs/") and self.path.endswith("/retry"):
            try:
                job = retry_job(self.path.split("/")[-2])
                return self.send({"id": job["id"], "status": "queued"}, 202)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if self.path != "/api/jobs":
            return self.send({"error": "接口不存在"}, 404)
        try:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("请求必须为 JSON")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 4096:
                raise ValueError("请求大小无效")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            ticker = symbol(payload.get("ticker"), payload.get("market", "auto"))
            peers = payload.get("peers", [])
            if not isinstance(peers, list) or len(peers) > 5:
                raise ValueError("最多比较 5 家同行")
            peers = [p for p in dict.fromkeys(symbol(p) for p in peers if p) if p != ticker]
            return self.send(queue_research(ticker, peers, payload.get("date", today())), 202)
        except (ValueError, TypeError) as exc:
            return self.send({"error": str(exc)}, 400)


def enrich_import(job_id):
    try:
        update(job_id, data_status="loading")
        data = company_data(JOBS[job_id]["ticker"])
        update(job_id, data=data, data_status="completed")
    except Exception:
        log.exception("Imported report data fetch failed")
        update(job_id, data_status="failed")


def main():
    parser = argparse.ArgumentParser(description="FundAnalyst local research UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-seed-data", action="store_true", help="Start without bundled research data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not args.no_seed_data and initialize_shared_data(STORE):
        log.info("Loaded bundled research archive into %s", STORE)
    load_history()
    for job in list(JOBS.values()):
        if job.get("imported") and not job.get("data"):
            update(job["id"], data_status="loading")
            POOL.submit(enrich_import, job["id"])
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"FundAnalyst Web: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

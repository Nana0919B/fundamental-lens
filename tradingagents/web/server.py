"""Loopback-only UI. Run with python -m tradingagents.web.server."""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt

from tradingagents.default_config import build_default_config
from tradingagents.report_brief import make_company_brief
from tradingagents.reporting import write_report_tree
from tradingagents.dataflows.research_analytics import enrich_snapshot
from tradingagents.dataflows.research_store import ResearchStore
from tradingagents.dataflows.vendors.sec_edgar import get_filing_documents
from tradingagents.dataflows.web_data import company_data, peer_data
from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.symbols import normalize_symbol, safe_ticker_component

ROOT = Path(__file__).parent / "static"
STORE = Path(build_default_config()["results_dir"]) / "web"
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")
log = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def symbol(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("请输入股票代码")
    return safe_ticker_component(normalize_symbol(value))


def persist(job):
    STORE.mkdir(parents=True, exist_ok=True)
    replace_file(
        STORE / f"{job['id']}.json",
        lambda p: Path(p).write_text(
            json.dumps(job, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        ),
    )


def update(job_id, **changes):
    with LOCK:
        JOBS[job_id].update(changes)
        persist(JOBS[job_id])


def run_job(job_id):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    try:
        job = JOBS[job_id]
        update(job_id, status="running", phase="正在获取公司资料与财务报表")
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
        update(job_id, research_snapshot=snapshot, snapshot_digest=digest, profile_changes=changes)
        update(
            job_id,
            peers_data=peers,
            phase="基本面、经营与财务指标智能体并行研究；随后进行多空辩论与风险评估",
        )
        config = build_default_config()
        # Isolate mutable graph, memory and checkpoints from CLI/other runs.
        config.update(
            results_dir=str(STORE / "engine" / job_id),
            data_cache_dir=str(STORE / "cache"),
            memory_log_path=str(STORE / "memory.md"),
            checkpoint_enabled=False,
        )
        graph = TradingAgentsGraph(
            selected_analysts=["fundamentals", "operating", "metrics"],
            config=config,
            research_snapshot=snapshot,
        )
        state, rating = graph.propagate(job["ticker"], job["date"])
        reports = {
            k: state.get(k, "")
            for k in (
                "fundamentals_report",
                "operating_report",
                "metrics_report",
                "investment_plan",
                "trader_investment_plan",
                "final_trade_decision",
            )
        }
        reports["company_brief"] = make_company_brief(snapshot, reports)
        write_report_tree(
            {**state, "company_brief": reports["company_brief"]},
            job["ticker"],
            STORE / "exports" / job_id,
            settings=graph.run_settings(),
        )
        archive.archive_reports(snapshot, reports)
        update(job_id, status="completed", phase="分析完成", reports=reports, rating=rating)
    except Exception as exc:
        log.exception("Research job %s failed", job_id)
        # Do not expose SDK errors containing headers, keys or internal URLs.
        update(
            job_id,
            status="failed",
            phase="分析未完成，可查看已获取的数据",
            error=f"{type(exc).__name__}：请检查模型服务、网络及终端日志后重试。",
        )


def load_history():
    if STORE.exists():
        for path in STORE.glob("*.json"):
            try:
                job = json.loads(path.read_text())
                if job["status"] in ("queued", "running"):
                    job.update(
                        status="failed", phase="服务已重启，请重新发起分析", error="上次任务被中断"
                    )
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
            with LOCK:
                jobs = [
                    {
                        k: v
                        for k, v in job.items()
                        if k not in ("data", "reports", "peers_data", "research_snapshot")
                    }
                    for job in JOBS.values()
                ]
            return self.send(
                {
                    "jobs": sorted(jobs, key=lambda j: j["created_at"], reverse=True),
                    "today": today(),
                }
            )
        if path.startswith("/api/peers/"):
            try:
                ticker = symbol(path.rsplit("/", 1)[-1])
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
            if not file.exists():
                return self.send({"error": "该任务尚无完整导出文件"}, 404)
            return self.send(file.read_bytes(), content_type="text/html; charset=utf-8")
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
            result["versions"] = archive.history(ticker) if ticker else []
            result["report_html"] = {
                k: MARKDOWN.render(v) for k, v in result.get("reports", {}).items()
            }
            return self.send(result)
        files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
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
            ticker = symbol(payload.get("ticker"))
            peers = payload.get("peers", [])
            if not isinstance(peers, list) or len(peers) > 5:
                raise ValueError("最多比较 5 家同行")
            peers = [p for p in dict.fromkeys(symbol(p) for p in peers if p) if p != ticker]
            with LOCK:
                if sum(j["status"] in ("running", "queued") for j in JOBS.values()) >= 3:
                    return self.send({"error": "已有 3 个任务，请等待完成"}, 429)
                job = {
                    "id": uuid.uuid4().hex,
                    "ticker": ticker,
                    "peers": peers,
                    "date": today(),
                    "created_at": datetime.now().astimezone().isoformat(),
                    "status": "queued",
                    "phase": "等待分析任务启动",
                }
                JOBS[job["id"]] = job
                persist(job)
            POOL.submit(run_job, job["id"])
            return self.send({"id": job["id"], "status": "queued"}, 202)
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
    parser = argparse.ArgumentParser(description="TradingAgents local research UI")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    load_history()
    for job in list(JOBS.values()):
        if job.get("imported") and not job.get("data"):
            update(job["id"], data_status="loading")
            POOL.submit(enrich_import, job["id"])
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"TradingAgents Web: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

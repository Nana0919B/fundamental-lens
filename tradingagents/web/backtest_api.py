"""Small persistent background runner, separate from live company research."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from tradingagents.web.valuation_backtest import run, save_json, validate

POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()
ACTIVE = set()


def records(store):
    folder = store / "backtests"
    result = []
    for p in sorted(folder.glob("*.json"), reverse=True):
        try:
            row = read(store, p.stem)
            result.append({k: v for k, v in row.items() if k != "result"})
        except (ValueError, OSError):
            continue
    return sorted(result, key=lambda x: x["created_at"], reverse=True)


def read(store, identifier):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("回测不存在")
    try:
        row = json.loads((store / "backtests" / f"{identifier}.json").read_text())
    except FileNotFoundError:
        raise ValueError("回测不存在") from None
    if row["status"] in ("running", "queued") and identifier not in ACTIVE:
        row.update(status="interrupted", error="服务重启中断此任务，请重新运行；原记录保留")
    return row


def create(store, payload):
    from copy import deepcopy

    if not isinstance(payload, dict):
        raise ValueError("请求必须是对象")
    payload = deepcopy(payload)
    # Public API accepts only server-resolved references, never fabricated provenance.
    if "research_template" in payload:
        raise ValueError("请通过已保存的历史研究或回测记录选择模板")
    if payload.get("template_run_id"):
        prior = read(store, payload["template_run_id"])
        template = prior["parameters"].get("research_template")
        if not template:
            raise ValueError("指定回测没有研究模板")
        payload["research_template"] = template
    elif payload.get("research_job_id"):
        identifier = payload["research_job_id"]
        if (
            not isinstance(identifier, str)
            or len(identifier) != 32
            or any(c not in "0123456789abcdef" for c in identifier)
        ):
            raise ValueError("研究来源不存在")
        try:
            job = json.loads((store / (identifier + ".json")).read_text())
        except FileNotFoundError:
            raise ValueError("研究来源不存在") from None
        value = job.get("valuation") or {}
        inputs = value.get("inputs") or {}
        if (
            not job.get("historical_context")
            or not inputs
            or inputs.get("method") == "company_fcf_bridge"
        ):
            raise ValueError("只可使用带历史截止日的经营预测研究，不能将当前研究倒灌历史")
        payload["research_template"] = {
            "source_job_id": identifier,
            "input_digest": value["input_digest"],
            "ticker": job["ticker"],
            "as_of_date": job["date"],
            "years": inputs["years"],
            "scenarios": inputs["scenarios"],
            "rationale": inputs["rationale"],
        }
    params = validate(payload)
    with LOCK:
        if ACTIVE:
            raise ValueError("已有回测运行中，请等待完成")
        row = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "queued",
            "parameters": params,
        }
        ACTIVE.add(row["id"])
        try:
            save_json(store / "backtests" / f"{row['id']}.json", row)
            POOL.submit(_execute, store, row)
        except Exception:
            ACTIVE.discard(row["id"])
            raise
    return row


def _execute(store, row):
    path = store / "backtests" / f"{row['id']}.json"
    try:
        row["status"] = "running"
        save_json(path, row)
        result = run(row["parameters"], store / "backtest-cache")
        row.update(
            result=result,
            status="completed"
            if not result["errors"]
            else "partial"
            if result["results"]
            else "failed",
        )
    except Exception as exc:
        row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            save_json(path, row)
        finally:
            with LOCK:
                ACTIVE.discard(row["id"])


def research_record(store, identifier, ticker, day):
    """Materialize a frozen backtest decision in the ordinary research workspace."""
    from copy import deepcopy

    from tradingagents.web.historical_valuation import digest

    row = read(store, identifier)
    matches = [r for r in row.get("result", {}).get("results", []) if r["ticker"] == ticker]
    if not matches:
        raise ValueError("回测中没有该股票")
    result = matches[0]
    values = [result["inspection"]] + [d["valuation"] for d in result["decisions"]]
    value = next((v for v in values if v["date"] == day), None)
    if not value or "research_job" not in value:
        raise ValueError("该日期无统一引擎研究快照；旧版回测需重新运行")
    job = deepcopy(value["research_job"])
    job.update(
        id=digest({"backtest": identifier, "ticker": ticker, "date": day})[:32],
        created_at=datetime.now(UTC).isoformat(),
        backtest_origin={
            "id": identifier,
            "ticker": ticker,
            "date": day,
            "input_digest": value["input_digest"],
            "parameters": deepcopy(row["parameters"]),
        },
    )
    return job

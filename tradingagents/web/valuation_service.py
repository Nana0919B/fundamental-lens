"""Valuation application boundary: immutable research in, valuation-only state out."""
import json
import logging
import math
import re
import uuid
from copy import deepcopy
from datetime import date
from pathlib import Path

from tradingagents.dataflows.files import replace_file

log = logging.getLogger(__name__)


class ValuationService:
    def __init__(self, *, store, jobs, lock, persist, update, check_pause, pause_exception):
        self.store, self.jobs, self.lock = store, jobs, lock
        self.persist, self._update = persist, update
        self.check_pause, self.pause_exception = check_pause, pause_exception

    def update(self, job_id, **changes):
        if set(changes) - {"automatic_valuation", "valuation"}:
            raise ValueError("Valuation cannot modify research state")
        self._update(job_id, **changes)

    def reference_quote(self, job):
        """Expose archived factual prices even when forecasting needs review."""
        record = job.get("automatic_valuation")
        if not isinstance(record, dict):
            return None
        job_id, run_id = job.get("id", ""), record.get("run_id", "")
        if not self.store or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v)
                                     for v in (job_id, run_id)):
            return None
        try:
            quote = json.loads((self.store / "valuation-research" / job_id / run_id / "quote.json").read_text())
            price = quote["price"]
            if isinstance(price, bool) or not math.isfinite(float(price)) or float(price) <= 0:
                return None
            if date.fromisoformat(quote["date"]) > date.fromisoformat(job["date"]):
                return None
            if not isinstance(quote.get("currency"), str) or not quote["currency"] or not isinstance(quote.get("source"), str) or not quote["source"]:
                return None
            return quote
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def calculate(job, payload):
        from tradingagents.web.valuation import calculate
        return calculate(job, payload)

    @staticmethod
    def prepare(job):
        try:
            from tradingagents.web.valuation import prepare
            return prepare(deepcopy(job))
        except Exception:
            log.exception("Valuation preparation failed; research remains available")
            return {"inputs": {}, "saved": job.get("valuation"), "currency": "",
                    "missing": ["估值模块暂不可用，其他研究板块可继续查看"],
                    "warnings": [], "evidence": {}, "module_error": True}

    def save(self, job_id, result):
        result["id"] = uuid.uuid4().hex
        folder = self.store / "valuations" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        replace_file(folder / (result["id"] + ".json"), lambda p: Path(p).write_text(
            json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8"))
        return result


    def run(self, job_id):
        """Research completion is durable even if valuation verification fails."""
        run_id = uuid.uuid4().hex
        with self.lock:
            job = deepcopy(self.jobs[job_id])
        def progress(phase):
            self.check_pause(job_id)
            self.update(job_id, automatic_valuation={"status": "running", "phase": phase, "run_id": run_id})
        try:
            from tradingagents.web.automatic_valuation import binding, generate
            progress("正在自动核实公司估值资料")
            result = generate(job, self.store / "valuation-research" / job_id / run_id, progress)
            result["run_id"] = run_id
            with self.lock:
                if binding(self.jobs[job_id]) != binding(job):
                    raise ValueError("研究资料已变化，请重新生成估值")
            if result["status"] == "completed":
                candidate = {**job, "automatic_valuation": result}
                valuation = self.calculate(candidate, result["prepared"]["inputs"])
                self.save(job_id, valuation)
                self.update(job_id, automatic_valuation=result, valuation=valuation)
            else:
                self.update(job_id, automatic_valuation=result)
        except self.pause_exception:
            from tradingagents.web.automatic_valuation import save
            save(self.store / "valuation-research" / job_id / run_id / "result.json",
                 {"status": "paused", "binding": binding(job), "gaps": []})
            self.update(job_id, automatic_valuation={"status": "paused", "phase": "估值已暂停", "run_id": run_id})
        except Exception as exc:
            log.exception("Automatic valuation %s failed", job_id)
            message = str(exc)[:1200] if isinstance(exc, ValueError) else type(exc).__name__ + "：自动核实未完成，可重试；研究报告已保留"
            self.update(job_id, automatic_valuation={"status": "failed", "gaps": [message], "run_id": run_id})


    def queue(self, job_id, submit):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.get("status") != "completed":
                raise ValueError("请先完成研究报告")
            if job.get("historical_context"):
                raise ValueError("历史回测记录不可覆盖为今天生成的研究假设")
            if job.get("automatic_valuation", {}).get("status") in ("queued", "running"):
                return {"id": job_id, "status": job["automatic_valuation"]["status"]}
            changed = {**job, "pause_requested": False, "automatic_valuation": {"status": "queued", "phase": "等待公司估值核实"}}
            self.persist(changed)
            self.jobs[job_id] = changed
        submit(job_id)
        return {"id": job_id, "status": "queued"}

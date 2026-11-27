"""Automation must fail closed on missing/altered evidence, not prefill a DCF."""

from copy import deepcopy

import pytest

from tradingagents.web import automatic_valuation as av, server, valuation


@pytest.fixture
def sample():
    text = "Revenue 1000; Cash 100; Debt 50; All common shares 10; Investments 20; Other claims 5. No preferred stock outstanding."
    documents = [
        {
            "id": "S1",
            "text": text,
            "filed": "2026-10-20",
            "period": "2026-09-30",
            "form": "10-Q",
            "url": "https://www.sec.gov/example",
            "sha256": "test",
        }
    ]
    job = {
        "id": "company",
        "ticker": "TEST",
        "date": "2026-11-27",
        "status": "completed",
        "reports": dict.fromkeys(av.REPORTS, "Company operating research evidence"),
        "research_snapshot": {
            "company": {"profile": {"currency": "USD", "financialCurrency": "USD"}}
        },
    }
    plan = {
        "status": "ready",
        "gaps": [],
        "currency": "USD",
        "financial_date": "2026-09-30",
        "complex_capital_structure": False,
        "facts": {},
        "checks": [
            {
                "topic": topic,
                "source_id": "S1",
                "quote": text,
                "status": "resolved",
                "treatment": "A specific explanation with evidence",
            }
            for topic in av.CHECKS
        ],
        "operating_cash_ratio": 0.03,
        "rationale": "Company-specific rationale with evidence",
        **dict.fromkeys(
            ("business_model", "forecast_reasoning", "equity_reasoning", "limitations"),
            "A company-specific explanation based on operating evidence",
        ),
        "scenarios": {},
    }
    for k, amount in zip(av.FACTS, (1000, 100, 50, 10, 20, 5), strict=False):
        plan["facts"][k] = [
            {
                "label": k,
                "amount": amount,
                "scale": 1000000,
                "sign": 1,
                "period_end": "2026-09-30",
                "source_id": "S1",
                "quote": text,
            }
        ]
    for name, growth in (("bear", 0.03), ("base", 0.07), ("bull", 0.12)):
        plan["scenarios"][name] = {
            "growth": [growth] * 10,
            "margins": [0.2] * 10,
            "da_ratios": [0.03] * 10,
            "capex_ratios": [0.06] * 10,
            "nwc_ratio": 0.1,
            "tax_rate": 0.2,
            "wacc": 0.1,
            "terminal_growth": 0.02,
            "terminal_roic": 0.15,
            "asset_haircut": 0.1,
        }
    quote = {"currency": "USD", "price": 50.0, "date": "2026-11-25"}
    return job, plan, documents, quote


def test_source_components_compile_into_shared_engine(sample):
    job, plan, docs, quote = sample
    prepared = av.compile_plan(job, plan, docs, quote)
    job["automatic_valuation"] = {
        "status": "completed",
        "binding": av.binding(job),
        "prepared": prepared,
    }
    setup = valuation.prepare(job)
    assert not setup["missing"]  # verified facts can fill absent vendor fields
    assert setup["inputs"]["shares"] == 10e6
    result = valuation.calculate(job, setup["inputs"])
    assert result["sensitivity"]["prices"][2][2] == pytest.approx(
        result["scenarios"]["base"]["price"]
    )
    assert result["inputs"]["automatic_research"]["plan_digest"] == av.digest(plan)
    changed = deepcopy(result["inputs"])
    changed["shares"] *= 2
    with pytest.raises(ValueError, match="不可改写"):
        valuation.calculate(job, changed)
    job["reports"]["operating_report"] += " changed"
    with pytest.raises(ValueError, match="重新生成"):
        av.prepared(job)


def test_contingent_claim_stress_preserves_operating_forecasts(sample):
    job, plan, docs, quote = sample
    baseline = av.compile_plan(job, deepcopy(plan), docs, quote)
    evidence = "A guarantee of up to 200 million is payable only on counterparty default."
    docs[0]["text"] += " " + evidence
    plan["contingent_claims"] = [{
        "label": "Counterparty guarantee", "amount": 200, "scale": 1000000,
        "source_id": "S1", "quote": evidence,
        "trigger": "Payments depend on counterparty default and contractual conditions.",
        "treatment": "Unknown additional net loss, excluding any loss already recognized in the equity bridge.",
    }]
    result = av.compile_plan(job, plan, docs, quote)
    risk = result["inputs"]["automatic_research"]["contingent_claims"][0]
    assert [r["per_share_reduction"] for r in risk["stress"]] == [0, 5, 10, 20]
    assert result["inputs"]["scenarios"] == baseline["inputs"]["scenarios"]
    assert valuation.project(result["inputs"], result["inputs"]["scenarios"]["base"])["price"] == valuation.project(baseline["inputs"], baseline["inputs"]["scenarios"]["base"])["price"]
    job["automatic_valuation"] = {"status": "completed", "binding": av.binding(job), "prepared": result}
    first = valuation.calculate(job, result["inputs"])
    browser = deepcopy(result["inputs"])
    browser["automatic_research"]["contingent_claims"][0]["maximum_exposure"] = 200000000.0
    again = valuation.calculate(job, browser)
    assert first["input_digest"] == again["input_digest"]
    assert first["scenarios"] == again["scenarios"]
    plan["contingent_claims"][0]["amount"] = 300
    with pytest.raises(ValueError, match="原文数值"):
        av.compile_plan(job, plan, docs, quote)


def test_resume_proposal_requires_same_research_and_source_hashes(sample, tmp_path):
    job, plan, docs, _ = sample
    prior = tmp_path / "previous"
    av.save(prior / "result.json", {"status": "needs_review", "binding": av.binding(job), "gaps": ["quantify guarantee"]})
    av.save(prior / "S1.json", docs[0])
    av.save(prior / "candidate-1.json", plan)
    target = tmp_path / "next"
    assert av.previous_proposal(job, docs, target)["plan"] == plan
    changed = deepcopy(docs)
    changed[0]["sha256"] = "changed"
    assert av.previous_proposal(job, changed, target) is None
    job["reports"]["operating_report"] += " new evidence"
    assert av.previous_proposal(job, docs, target) is None
    job["reports"]["operating_report"] = job["reports"]["operating_report"].removesuffix(" new evidence")
    av.save(tmp_path / "completed" / "result.json", {"status": "completed", "binding": av.binding(job)})
    assert av.previous_proposal(job, docs, target) is None


@pytest.mark.parametrize(
    "fault", ["quote", "number", "future", "class", "preferred", "currency", "duplicate"]
)
def test_invalid_or_unresolved_facts_cannot_produce_valuation(sample, fault):
    job, plan, docs, quote = sample
    if fault == "quote":
        plan["facts"]["cash"][0]["quote"] = "An invented quote that is not in the original document"
    if fault == "number":
        plan["facts"]["cash"][0]["amount"] = 777
    if fault == "future":
        docs[0]["filed"] = job["date"]
    if fault == "class":
        plan["checks"][0]["status"] = "unresolved"
    if fault == "preferred":
        plan["complex_capital_structure"] = True
    if fault == "currency":
        quote["currency"] = "HKD"
    if fault == "duplicate":
        plan["facts"]["cash"] *= 2
    with pytest.raises(ValueError):
        av.compile_plan(job, plan, docs, quote)


def test_pending_never_falls_back_to_default_valuation(sample):
    job, *_ = sample
    job["automatic_valuation"] = {"status": "running"}
    setup = valuation.prepare(job)
    assert setup["missing"]
    with pytest.raises(ValueError, match="尚未完成"):
        valuation.calculate(job, setup["inputs"])


def test_background_failure_preserves_research_and_prior_valuation(sample, monkeypatch, tmp_path):
    job, *_ = sample
    job["valuation"] = {"id": "prior", "inputs": {"example": "preserved"}}
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "JOBS", {job["id"]: deepcopy(job)})

    def fail(*args):
        raise ValueError("Missing a shareholder note")

    monkeypatch.setattr(av, "generate", fail)
    server.run_automatic_valuation(job["id"])
    saved = server.JOBS[job["id"]]
    assert saved["status"] == "completed"
    assert saved["reports"] == job["reports"]
    assert saved["valuation"] == job["valuation"]
    assert saved["automatic_valuation"]["status"] == "failed"


def test_background_success_persists_shared_result(sample, monkeypatch, tmp_path):
    job, plan, docs, quote = sample
    prepared = av.compile_plan(job, plan, docs, quote)
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "JOBS", {job["id"]: deepcopy(job)})
    monkeypatch.setattr(
        av,
        "generate",
        lambda *args: {"status": "completed", "binding": av.binding(job), "prepared": prepared},
    )
    server.run_automatic_valuation(job["id"])
    saved = server.JOBS[job["id"]]
    assert saved["automatic_valuation"]["status"] == "completed"
    assert saved["valuation"]["version"] == valuation.VERSION
    assert list((tmp_path / "valuations" / job["id"]).glob("*.json"))
    assert saved["research_snapshot"] == job["research_snapshot"]


def test_double_request_is_idempotent_and_historical_is_blocked(sample, monkeypatch, tmp_path):
    job, *_ = sample
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "JOBS", {job["id"]: deepcopy(job)})
    calls = []

    class Pool:
        def submit(self, *args):
            calls.append(args)

    monkeypatch.setattr(server, "VALUATION_POOL", Pool())
    for _ in range(2):
        assert server.queue_automatic_valuation(job["id"])["status"] == "queued"
    assert len(calls) == 1
    server.JOBS[job["id"]]["historical_context"] = {"as_of_date": job["date"]}
    with pytest.raises(ValueError, match="历史回测"):
        server.queue_automatic_valuation(job["id"])


def test_application_tool_loop_verifies_and_saves_plan(sample, tmp_path):
    import json

    from langchain_core.messages import AIMessage

    job, plan, docs, quote = sample

    class Model:
        def __init__(self):
            self.count = 0

        def bind_tools(self, tools, **kwargs):
            assert {t["name"] for t in tools} == {"read_source", "submit_plan"}
            return self

        def invoke(self, messages):
            self.count += 1
            if self.count == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "read",
                            "name": "read_source",
                            "args": {"source_id": "S1", "query": "shares", "start": 0},
                        }
                    ],
                )
            if self.count == 2:
                assert "All common shares" in messages[-1].content
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "submit",
                            "name": "submit_plan",
                            "args": {"plan_json": json.dumps(plan)},
                        }
                    ],
                )
            return AIMessage(content='{"approved":true,"gaps":[]}')

    progress = []
    result = av.synthesize(job, docs, quote, Model(), progress.append, tmp_path)
    assert result["status"] == "completed"
    assert (tmp_path / "candidate-1.json").exists()
    assert (tmp_path / "review-1.json").exists()
    assert "复核" in progress[-1]


def test_new_research_finishes_reports_then_invokes_valuation(sample, monkeypatch, tmp_path):
    from tradingagents.graph import trading_graph

    job, *_ = sample
    job["peers"] = []
    job["status"] = "queued"
    monkeypatch.setattr(server, "STORE", tmp_path)
    monkeypatch.setattr(server, "JOBS", {job["id"]: deepcopy(job)})
    monkeypatch.setattr(server, "build_default_config", lambda: {"trendradar_enabled": False})
    monkeypatch.setattr(server, "build_market_environment", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "write_report_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "make_company_brief", lambda *args: "Company brief")

    class Archive:
        def __init__(self, *args):
            pass

        def archive_reports(self, *args):
            pass

    class Graph:
        def __init__(self, **kwargs):
            pass

        def propagate(self, *args):
            return (
                {**job["reports"], "news_report": "News", "final_trade_decision": "Hold"},
                "Hold",
            )

        def run_settings(self):
            return {}

    monkeypatch.setattr(server, "ResearchStore", Archive)
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    calls = []
    monkeypatch.setattr(
        server,
        "queue_automatic_valuation",
        lambda job_id: calls.append(deepcopy(server.JOBS[job_id])),
    )
    server.run_job(job["id"])
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    assert calls[0]["reports"]["operating_report"]
    assert calls[0]["reports"]["company_brief"] == "Company brief"


def test_gap_review_requires_real_source_quote(sample):
    import json

    from langchain_core.messages import AIMessage

    job, _, docs, _ = sample

    class Reviewer:
        def invoke(self, messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "blocking": False,
                        "resolution": "This is a supported modeling convention, not a missing original amount.",
                        "evidence": [
                            {
                                "source_id": "S1",
                                "quote": "Invented claim that is not present in the source document",
                            }
                        ],
                    }
                )
            )

    with pytest.raises(ValueError, match="引用必须逐字"):
        av.review_gap({"gaps": ["unknown fact"]}, docs, Reviewer(), job["date"])


def test_nonblocking_gap_gets_source_review_before_plan_generation(sample, tmp_path):
    import json

    from langchain_core.messages import AIMessage

    job, plan, docs, quote = sample

    class Model:
        count = 0

        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            self.count += 1
            if self.count == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "gap",
                            "name": "submit_plan",
                            "args": {
                                "plan_json": json.dumps(
                                    {
                                        "status": "needs_review",
                                        "gaps": [
                                            "Uncertainty about whether preferred stock exists"
                                        ],
                                    }
                                )
                            },
                        }
                    ],
                )
            if self.count == 2:
                return AIMessage(
                    content=json.dumps(
                        {
                            "blocking": False,
                            "resolution": "The source explicitly states there is no outstanding preferred stock.",
                            "evidence": [{"source_id": "S1", "quote": docs[0]["text"]}],
                        }
                    )
                )
            if self.count == 3:
                assert "gap_review" in messages[-1].content
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "plan",
                            "name": "submit_plan",
                            "args": {"plan_json": json.dumps(plan)},
                        }
                    ],
                )
            return AIMessage(content='{"approved":true,"gaps":[]}')

    result = av.synthesize(job, docs, quote, Model(), lambda phase: None, tmp_path)
    assert result["status"] == "completed"
    assert (tmp_path / "gap-review-0.json").exists()
    assert result["prepared"]["inputs"]["shares"] == 10e6


def test_historical_split_after_share_disclosure_cannot_silently_mix_units(sample):
    job, plan, documents, quote = sample
    job['research_snapshot']['point_in_time'] = {'version': 'company-pit-1.0'}
    quote['known_splits'] = [{'date': '2026-11-01', 'ratio': 4}]
    with pytest.raises(ValueError, match='拆股'):
        av.compile_plan(job, plan, documents, quote)

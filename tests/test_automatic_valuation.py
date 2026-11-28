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
    review = {"blocking": False, "resolution": "Source-backed unresolved loss convention", "evidence": [{"source_id": "S1", "quote": docs[0]["text"]}]}
    av.save(prior / "gap-review-2.json", review)
    assert av.previous_proposal(job, docs, target)["gap_reviews"] == [review]
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


def test_shared_contingency_rule_and_source_gap_forwarding(sample):
    import json

    from langchain_core.messages import AIMessage
    job, plan, docs, _ = sample
    compiled = av.compile_plan(job, plan, docs, {"price": 100, "currency": "USD", "date": "2026-11-26"})
    gap = {"blocking": False, "resolution": "Unknown losses cannot be fabricated", "evidence": [{"source_id": "S1", "quote": docs[0]["text"]}]}

    class Reviewer:
        def invoke(self, messages):
            assert messages[0].content == av.CONTINGENCY_CONVENTION
            payload = json.loads(messages[-1].content)
            if "plan" in payload:
                assert payload["source_checked_gap_reviews"] == [gap]
                assert payload["computed_contingent_stress"] == []
                assert payload["contingency_convention"] == av.CONTINGENCY_CONVENTION
                return AIMessage(content='{"approved":true,"gaps":[]}')
            return AIMessage(content=json.dumps(gap))

    assert av.review_gap({"gaps": ["unknown loss"]}, docs, Reviewer(), job["date"])["blocking"] is False
    assert av.review_plan(job, plan, compiled, Reviewer(), lambda _: None, [gap])["approved"]
    assert av.CONTINGENCY_CONVENTION in av.PROMPT


def test_archived_evidence_rejects_tampering(sample, tmp_path):
    import hashlib

    from bs4 import BeautifulSoup
    job, _, _, _ = sample
    origin = tmp_path / "old"
    raw = b'<html><body>Evidence without invented amounts</body></html>'
    doc = {"id": "S1", "filed": "2026-10-20", "text": av.normalize(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)), "sha256": hashlib.sha256(raw).hexdigest()}
    av.save(origin / "result.json", {"binding": av.binding(job), "status": "needs_review"})
    av.save(origin / "S1.json", doc)
    (origin / "S1.html").write_bytes(raw)
    av.save(origin / "quote.json", {"price": 100, "date": "2026-11-26", "currency": "USD"})
    documents, _, quote = av.saved_evidence(job, tmp_path / "new")
    assert documents == [doc]
    assert quote["price"] == 100
    (origin / "S1.html").write_bytes(b'altered')
    with pytest.raises(ValueError, match="校验失败"):
        av.saved_evidence(job, tmp_path / "another")


@pytest.mark.parametrize("approved", [True, False])
def test_retry_reviews_exact_saved_forecast_without_refetch(sample, tmp_path, monkeypatch, approved):
    import json

    from langchain_core.messages import AIMessage

    job, plan, docs, quote = sample
    original = deepcopy(plan)
    compiled_plan = deepcopy(original)
    av.compile_plan(job, compiled_plan, docs, quote)  # derived aliases only
    monkeypatch.setattr(av, "saved_evidence", lambda *_: (docs, [], quote))
    monkeypatch.setattr(av, "previous_proposal", lambda *_: {"plan": plan, "run_id": "old"})
    monkeypatch.setattr(av, "collect", lambda *_: pytest.fail("must use archived filings"))
    monkeypatch.setattr(av, "get_quote", lambda *_: pytest.fail("must use archived quote"))
    fallback = []
    def synthesize(*args, **kwargs):
        fallback.append(True)
        return {"status": "needs_review", "gaps": ["genuine missing fact"]}
    monkeypatch.setattr(av, "synthesize", synthesize)

    class Reviewer:
        def invoke(self, messages):
            assert json.loads(messages[-1].content)["plan"] == compiled_plan
            if messages[0].content.startswith("你只修复"):
                return AIMessage(content='{"status":"needs_review","gaps":["genuine missing fact"]}')
            return AIMessage(content=json.dumps({"approved": approved, "gaps": [] if approved else ["genuine missing fact"]}))

    result = av.generate(job, tmp_path, llm=Reviewer())
    assert plan == original
    assert not fallback  # saved forecasts must never be regenerated to clear review
    assert result["status"] == ("completed" if approved else "needs_review")


def test_short_treatment_reports_format_problem_not_missing_evidence(sample):
    job, plan, docs, quote = sample
    plan['checks'][0]['treatment'] = '已核实。'
    with pytest.raises(ValueError, match='处理说明过短'):
        av.compile_plan(job, plan, docs, quote)
    plan['checks'][0]['treatment'] = '已核实原文，采用已披露的全部普通股数量。'
    assert av.compile_plan(job, plan, docs, quote)['inputs']['shares'] > 0


def test_invalid_cached_proposal_returns_to_repair_without_bypassing_checks(sample, tmp_path, monkeypatch):
    job, plan, docs, quote = sample
    plan['facts']['other_claims'] = []
    forecast = deepcopy(plan['scenarios'])
    monkeypatch.setattr(av, 'saved_evidence', lambda *_: (docs, [], quote))
    monkeypatch.setattr(av, 'previous_proposal', lambda *_: {'plan': plan, 'unresolved_gaps': [], 'run_id': 'old'})
    def repair(candidate, feedback, *args):
        assert '缺少原始依据' in feedback[0]
        assert candidate['scenarios'] == forecast
        return None, ['still requires independent review']
    monkeypatch.setattr(av, 'repair_equity_evidence', repair)
    result = av.generate(job, tmp_path, llm=object())
    assert result['status'] == 'needs_review'
    assert (tmp_path / 'replay-validation.json').exists()


def test_gap_review_includes_annual_note_beyond_initial_text(sample):
    import json

    from langchain_core.messages import AIMessage

    job, _, docs, _ = sample
    quote = 'Deferred tax assets are recorded net of tax contingencies in the annual report.'
    annual = {**docs[0], 'id': 'S3', 'text': ('Earlier report content. ' * 20000) + quote}
    class Reviewer:
        def invoke(self, messages):
            sources = json.loads(messages[-1].content)['sources']
            assert [s['id'] for s in sources] == ['S1', 'S3']
            assert quote not in sources[1]['text']
            assert quote in json.dumps(sources[1]['financial_notes'])
            return AIMessage(content=json.dumps({'blocking': False, 'resolution': 'The annual note is available and must be considered alongside the quarterly filing.', 'evidence': [{'source_id': 'S3', 'quote': quote}]}))
    assert not av.review_gap({'gaps': ['S3 net deferred tax overlap']}, [docs[0], annual], Reviewer(), job['date'])['blocking']


def test_treatment_repair_cannot_modify_financial_inputs(sample):
    import json

    from langchain_core.messages import AIMessage

    _, plan, _, _ = sample
    plan['checks'][0]['treatment'] = '已核实。'
    original = deepcopy(plan)
    class Editor:
        def invoke(self, messages):
            return AIMessage(content=json.dumps({'treatments': {'share_classes': '依据原文采用全部已披露普通股数量，避免遗漏股份类别。'}}))
    repaired = av.repair_treatments(plan, Editor())
    assert plan == original
    assert repaired['facts'] == original['facts']
    assert repaired['scenarios'] == original['scenarios']
    for before, after in zip(original['checks'], repaired['checks'], strict=True):
        assert {k: v for k, v in before.items() if k != 'treatment'} == {k: v for k, v in after.items() if k != 'treatment'}
    class BadEditor:
        def invoke(self, messages):
            return AIMessage(content='{"treatments":{"share_classes":"不得修改财务输入及预测参数。"},"scenarios":{}}')
    with pytest.raises(ValueError, match='不能修改其他字段'):
        av.repair_treatments(plan, BadEditor())
    assert plan == original


def test_cached_text_repair_still_requires_independent_review(sample, tmp_path, monkeypatch):
    import json

    from langchain_core.messages import AIMessage

    job, plan, docs, quote = sample
    plan['checks'][0]['treatment'] = '已核实。'
    monkeypatch.setattr(av, 'saved_evidence', lambda *_: (docs, [], quote))
    monkeypatch.setattr(av, 'previous_proposal', lambda *_: {'plan': plan, 'run_id': 'old'})
    monkeypatch.setattr(av, 'synthesize', lambda *a, **k: pytest.fail('no need to regenerate forecasts for text length'))
    calls = []
    class Reviewer:
        def invoke(self, messages):
            if messages[0].content.startswith('仅补充'):
                calls.append('text')
                return AIMessage(content=json.dumps({'treatments': {'share_classes': '依据已核实原文采用全部普通股数量，避免遗漏股份类别。'}}))
            calls.append('independent_review')
            return AIMessage(content='{"approved":true,"gaps":[]}')
    result = av.generate(job, tmp_path, llm=Reviewer())
    assert result['status'] == 'completed'
    assert calls == ['text', 'independent_review']
    assert (tmp_path / 'candidate-format-repair.json').exists()


def test_evidence_patch_cannot_change_forecast_parameters(sample):
    import json

    from langchain_core.messages import AIMessage

    _, plan, docs, _ = sample
    before = deepcopy(plan)
    class BadEditor:
        def invoke(self, messages):
            return AIMessage(content='{"status":"ready","patch":{"scenarios":{}}}')
    with pytest.raises(ValueError, match='不能修改预测参数'):
        av.repair_equity_evidence(plan, ['missing investment'], docs, BadEditor())
    class Editor:
        def invoke(self, messages):
            return AIMessage(content=json.dumps({'status': 'ready', 'patch': {'facts': {'non_operating_assets': before['facts']['non_operating_assets']}, 'equity_reasoning': 'Use the source carrying value once, without duplicating cash investments.'}}))
    repaired, gaps = av.repair_equity_evidence(plan, ['missing investment'], docs, Editor())
    assert not gaps
    assert repaired['scenarios'] == before['scenarios']
    assert repaired['operating_cash_ratio'] == before['operating_cash_ratio']
    assert plan == before


def test_fact_repair_requires_source_validation_and_new_independent_review(sample, tmp_path, monkeypatch):
    import json

    from langchain_core.messages import AIMessage

    job, plan, docs, quote = sample
    correct = deepcopy(plan['facts']['non_operating_assets'])
    plan['facts']['non_operating_assets'][0]['amount'] = 0
    forecast = deepcopy(plan['scenarios'])
    monkeypatch.setattr(av, 'saved_evidence', lambda *_: (docs, [], quote))
    monkeypatch.setattr(av, 'previous_proposal', lambda *_: {'plan': plan, 'run_id': 'old'})
    monkeypatch.setattr(av, 'synthesize', lambda *a, **k: pytest.fail('do not regenerate forecasts'))
    calls = []
    class Reviewer:
        def invoke(self, messages):
            payload = json.loads(messages[-1].content)
            if messages[0].content.startswith('你只修复'):
                calls.append('source_patch')
                return AIMessage(content=json.dumps({'status': 'ready', 'patch': {'facts': {'non_operating_assets': correct}}}))
            approved = payload['plan']['facts']['non_operating_assets'][0]['amount'] == 20
            calls.append('review_good' if approved else 'review_bad')
            return AIMessage(content=json.dumps({'approved': approved, 'gaps': [] if approved else ['missing disclosed investment']}))
    result = av.generate(job, tmp_path, llm=Reviewer())
    assert result['status'] == 'completed'
    assert calls == ['review_bad', 'source_patch', 'review_good']
    assert result['prepared']['inputs']['non_operating_assets'] == 20e6
    for scenario, fields in forecast.items():
        for key, value in fields.items():
            assert result['plan']['scenarios'][scenario][key] == value
    assert (tmp_path / 'evidence-repair-review.json').exists()

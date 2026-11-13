"""Evidence and attribution contracts for the independent financial analysts."""

import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.analysts.financial_specialists import (
    FinancialSpecialistReport,
    create_financial_specialist,
)


@pytest.mark.parametrize(
    "focus,key", [("operating", "operating_report"), ("metrics", "metrics_report")]
)
@pytest.mark.parametrize("structured", [True, False])
def test_specialist_provenance_causes_and_missing_data(focus, key, structured):
    calls = []
    report = FinancialSpecialistReport(
        conclusion="资料有限，不能判断增长驱动。",
        findings=[],
        change_explanations=[
            {
                "explanation": "可能与产品结构有关，但没有分部数据。",
                "basis": "hypothesis",
                "evidence": "分部数据未提供",
                "verification": "核查原始年报的分部披露",
            }
        ],
        risks=[{"conclusion": "无法评估现金转换风险", "evidence": "现金流报表缺失"}],
        limitations=["现金流和分部数据缺失"],
        follow_up_questions=["核查季度现金流是否为累计值"],
    )

    class LLM:
        def with_structured_output(self, *args):
            if not structured:
                raise NotImplementedError

            class Structured:
                def invoke(self, prompt):
                    calls.append(prompt)
                    return report

            return Structured()

        def invoke(self, prompt):
            calls.append(prompt)
            return AIMessage(
                content="## 核心结论\n证据不足\n## 变化原因与待验证解释\n待验证假设\n## 风险提示\n数据缺失"
            )

        def bind_tools(self, *args):
            pytest.fail("Specialists must not fetch data")

    snapshot = {
        "id": "frozen-123",
        "company": {
            "profile": {},
            "statements": {},
            "source": "Yahoo",
            "retrieved_at": "2026-11-13T12:00:00+08:00",
        },
        "as_of_date": "2026-11-13",
        "peers": [],
    }
    result = create_financial_specialist(LLM(), focus)({"research_snapshot": snapshot})
    text = result[key]
    assert "frozen-123" in text and "Yahoo" in text
    assert "待验证假设" in text
    assert len(calls) == 1
    prompt = str(calls[0])
    assert "not proof of a business cause" in prompt
    assert "Do not infer multi-year metric trends" in prompt
    assert "123" in prompt


def test_specialists_require_snapshot():
    class LLM:
        def with_structured_output(self, *args):
            raise NotImplementedError

    with pytest.raises(ValueError, match="snapshot"):
        create_financial_specialist(LLM(), "operating")({})


def test_report_links_only_existing_evidence_ids():
    report = FinancialSpecialistReport(
        conclusion="结论",
        findings=[
            {
                "conclusion": "ROE 有值",
                "evidence": "供应商指标",
                "evidence_refs": ["profile.returnOnEquity", "invented.id"],
            }
        ],
        change_explanations=[],
        risks=[],
        limitations=[],
        follow_up_questions=[],
    )

    class LLM:
        def with_structured_output(self, *args):
            return self

        def invoke(self, prompt):
            return report

    result = create_financial_specialist(LLM(), "metrics")(
        {
            "research_snapshot": {
                "id": "one",
                "as_of_date": "2026-11-13",
                "company": {"profile": {}, "statements": {}},
                "evidence": [{"id": "profile.returnOnEquity"}],
            }
        }
    )["metrics_report"]
    assert "[profile.returnOnEquity](#evidence-profile.returnOnEquity)" in result
    assert "invented.id" not in result

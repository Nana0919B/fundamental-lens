"""Company-oriented brief composed from existing analyst reports, without another LLM call."""

import re


def section(text, headings):
    pattern = r"^##?\s+(?:" + "|".join(re.escape(h) for h in headings) + r")\s*\n(.*?)(?=^##?\s|\Z)"
    match = re.search(pattern, text or "", re.M | re.S | re.I)
    return match.group(1).strip() if match else "未找到独立章节，请查看下方完整分析报告及原始证据。"


def make_company_brief(snapshot, reports):
    company = snapshot["company"]
    profile = company.get("profile", {})
    operating = reports.get("operating_report", "")
    metrics = reports.get("metrics_report", "")
    return "\n\n".join(
        [
            "# 公司研究简报",
            "## 公司与研究范围",
            f"{profile.get('longName') or company['symbol']}（{company['symbol']}） · "
            f"{profile.get('industry') or '行业未提供'}。本次结论基于共享快照，不包含未获取的分部、地区及行业经营指标。",
            "## 经营判断",
            section(operating, ["核心结论", "Conclusion", "Executive Summary"]),
            "## 财务质量与估值",
            section(metrics, ["核心结论", "Conclusion", "Executive Summary"]),
            "## 主要风险",
            "### 经营风险",
            section(operating, ["风险提示", "Risks"]),
            "### 财务风险",
            section(metrics, ["风险提示", "Risks"]),
            "## 待验证事项",
            section(operating, ["后续核查问题", "Follow-up Questions"]),
            section(metrics, ["后续核查问题", "Follow-up Questions"]),
            "## 证据与版本",
            f"快照编号：{snapshot['id']}；来源：{company.get('source', '未提供')}；"
            f"采集时间：{company.get('retrieved_at', '未提供')}；公式版本：{snapshot.get('analytics', {}).get('formula_version', '未提供')}。"
            "简报复用已有智能体结论，不增加模型调用。引用定位不等同于因果判断已验证。",
        ]
    )

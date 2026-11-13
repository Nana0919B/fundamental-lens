"""Company-oriented brief composed from existing analyst reports, without another LLM call."""

import re


HEADINGS = {
    "核心结论",
    "事实与证据",
    "变化原因与待验证解释",
    "风险提示",
    "数据局限",
    "后续核查问题",
    "conclusion",
    "executive summary",
    "risks",
    "follow-up questions",
}


def heading(line):
    value = re.sub(r"^\s*#{1,6}\s*", "", line).strip().strip("*").strip()
    value = re.sub(r"^[一二三四五六七八九十0-9]+[、.．)）]\s*", "", value)
    return value.rstrip(":：").strip().casefold()


def section(text, headings):
    lines = (text or "").splitlines()
    desired = {value.casefold() for value in headings}
    start = next((i for i, line in enumerate(lines) if heading(line) in desired), None)
    if start is None:
        return "未找到独立章节，请查看下方完整分析报告及原始证据。"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if heading(lines[i]) in HEADINGS or re.match(r"^#{1,6}\s", lines[i])
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end]).strip()


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

import re
from urllib.parse import urlparse

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import take_turn, WRAP_UP
from langchain_core.messages import HumanMessage
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.tools import (
    get_global_news,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_news,
    get_global_news,
    get_macro_indicators,
    get_prediction_markets,
)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(start_date, end_date) for news about the {asset_label} under analysis, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + " Start with get_news for the target company before optional macro tools. "
            + "Do not repeatedly request a source that returned unavailable. Cite each factual event with "
            + "its publication date and source URL from the tool output. Do not invent URLs or events. "
            + " Current runs can use the official FRED public CSV without an API key. "
            + "Do not call the macro tool unavailable just because FRED_API_KEY is absent. "
            + "The public CSV serves latest revisions, not historical point-in-time data; report that distinction. "
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in TOOLS]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result, report = take_turn(prompt, llm, TOOLS, state["messages"])

        if not report and not getattr(result, "tool_calls", None):
            result, report = take_turn(
                prompt, llm, TOOLS, [*state["messages"], HumanMessage(WRAP_UP)]
            )
            if not isinstance(report, str) or not report.strip():
                raise ValueError("News Analyst returned an empty report after retry")
        if report:
            urls = []
            for message in state["messages"]:
                if getattr(message, "type", "") != "tool":
                    continue
                for url in re.findall(r"Link:\s*(https?://[^\s<>]+)", str(message.content)):
                    if url not in urls:
                        urls.append(url)
            if urls:
                report += "\n\n## 新闻工具返回的原始来源\n" + "\n".join(
                    f"- [{urlparse(url).hostname}]({url})" for url in urls[:30]
                )
            else:
                report += "\n\n> 本次工具未返回可定位的新闻原文链接，请核实报告中的事件，勿将推断视为已确认事实。"
        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node

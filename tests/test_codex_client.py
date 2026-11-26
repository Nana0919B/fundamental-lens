"""Local Codex adapter: role preservation, tools, schemas and process isolation."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from tradingagents.llm_clients import create_llm_client
from tradingagents.llm_clients.codex_client import CodexChatModel, CodexCLIError


@tool
def company_lookup(ticker: str) -> str:
    """Look up company facts."""
    return ticker


def model():
    return CodexChatModel(executable="/local/codex")


def stub(monkeypatch, response):
    seen = []

    def generate(self, prompt, schema=None):
        seen.append((prompt, schema))
        return response

    monkeypatch.setattr(CodexChatModel, "_run_cli", generate)
    return seen


def test_plain_response_and_history(monkeypatch):
    seen = stub(monkeypatch, "Report")
    messages = [
        HumanMessage("BA"),
        AIMessage(content="", tool_calls=[{"name": "company_lookup", "args": {"ticker": "BA"}, "id": "old"}]),
        ToolMessage("Actual evidence", name="company_lookup", tool_call_id="old"),
    ]
    assert model().invoke(messages).content == "Report"
    history = json.loads(seen[0][0].split("Conversation:\n")[1])
    assert history[-1]["role"] == "tool"
    assert history[-1]["tool_call_id"] == "old"
    assert history[1]["tool_calls"][0]["args"] == {"ticker": "BA"}
    assert seen[0][1] is None


def test_bound_tool_calls_are_returned_for_application_execution(monkeypatch):
    seen = stub(monkeypatch, json.dumps({"content": "", "tool_calls": [
        {"name": "company_lookup", "arguments": '{"ticker":"BA"}'},
    ]}))
    result = model().bind_tools([company_lookup]).invoke("Find BA")
    assert result.tool_calls[0]["name"] == "company_lookup"
    assert result.tool_calls[0]["args"] == {"ticker": "BA"}
    assert seen[0][1]["properties"]["tool_calls"]["items"]["properties"]["name"]["enum"] == ["company_lookup"]


def test_structured_output_uses_langchain_parser(monkeypatch):
    class Finding(BaseModel):
        conclusion: str
        evidence: list[str]

    stub(monkeypatch, json.dumps({"content": "", "tool_calls": [
        {"name": "Finding", "arguments": json.dumps({"conclusion": "Limited data", "evidence": ["Archived balance sheet"]})},
    ]}))
    result = model().with_structured_output(Finding).invoke("Assess evidence")
    assert isinstance(result, Finding)
    assert result.evidence == ["Archived balance sheet"]


@pytest.mark.parametrize("response", [
    "not json",
    '{"content":"","tool_calls":[{"name":"shell","arguments":"{}"}]}',
    '{"content":"","tool_calls":[{"name":"company_lookup","arguments":"[]"}]}',
    '{"content":null,"tool_calls":[]}',
])
def test_malformed_or_unknown_tool_requests_fail_closed(monkeypatch, response):
    stub(monkeypatch, response)
    with pytest.raises(CodexCLIError):
        model().bind_tools([company_lookup]).invoke("BA")


def test_required_tool_is_enforced(monkeypatch):
    stub(monkeypatch, '{"content":"answer","tool_calls":[]}')
    with pytest.raises(CodexCLIError):
        model().bind_tools([company_lookup], tool_choice="any").invoke("BA")


def test_no_tools_choice_and_stop(monkeypatch):
    seen = stub(monkeypatch, "Report STOP other text")
    result = model().bind_tools([company_lookup], tool_choice="none").invoke("BA", stop=["STOP"])
    assert result.content == "Report "
    assert seen[0][1] is None


def test_subprocess_uses_stdin_existing_login_and_isolated_directory(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.invalid")

    def run(command, **kw):
        assert kw["input"] == "private prompt"
        assert "private prompt" not in command
        assert "OPENAI_API_KEY" not in kw["env"]
        assert "OPENAI_BASE_URL" not in kw["env"]
        assert "--ignore-user-config" in command
        assert "--ephemeral" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "features.shell_tool=false" in command
        assert "--model" not in command
        cwd = Path(command[command.index("-C") + 1])
        assert not list(cwd.glob("AGENTS.md"))
        Path(command[command.index("--output-last-message") + 1]).write_text("OK")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    assert model()._run_cli("private prompt") == "OK"


def test_timeout_and_diagnostics_do_not_leak(monkeypatch):
    def timeout(*args, **kw):
        raise subprocess.TimeoutExpired(args[0], 120, stderr="secret prompt")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(CodexCLIError, match="timed out") as error:
        model()._run_cli("private prompt")
    assert "secret" not in str(error.value)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr="secret"))
    with pytest.raises(CodexCLIError, match="status 1") as error:
        model()._run_cli("private prompt")
    assert "secret" not in str(error.value)


def test_factory_and_endpoint_validation(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_CODEX_CLI", "/local/codex")
    client = create_llm_client("codex", "default")
    assert client.validate_model()
    assert client.get_llm().executable == "/local/codex"
    with pytest.raises(ValueError, match="backend_url"):
        create_llm_client("codex", "default", base_url="https://unrelated.invalid").get_llm()

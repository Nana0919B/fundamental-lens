"""Streaming keeps long proxy requests alive without changing agent results."""

import httpx
import pytest
from anthropic import APIStatusError
from anthropic.types import RawMessageDeltaEvent
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from pydantic import BaseModel

from tradingagents.llm_clients.anthropic_client import AnthropicClient, NormalizedChatAnthropic
from tradingagents.llm_clients.factory import build_llm_kwargs


def test_streaming_is_opt_in_and_anthropic_specific():
    assert "streaming" not in build_llm_kwargs({"llm_provider": "anthropic"})
    assert build_llm_kwargs({"llm_provider": "anthropic", "anthropic_streaming": True})["streaming"]
    assert "streaming" not in build_llm_kwargs({"llm_provider": "openai", "anthropic_streaming": True})


def test_streamed_text_is_aggregated_before_original_agents_read_it(monkeypatch):
    def chunks(self, messages, **kwargs):
        for text in ("研究", "结论", "完整"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    monkeypatch.setattr(NormalizedChatAnthropic, "_stream", chunks)
    llm = AnthropicClient(model="claude-sonnet-5", api_key="test", streaming=True).get_llm()
    assert llm.invoke("Analyze").content == "研究结论完整"


def test_streaming_preserves_structured_tool_arguments(monkeypatch):
    class Rating(BaseModel):
        rating: str

    def chunks(self, messages, **kwargs):
        assert kwargs["tools"][0]["name"] == "Rating"
        for call in (
            {"name": "Rating", "args": '{"rating":', "id": "call-one", "index": 0},
            {"name": None, "args": '"Hold"}', "id": None, "index": 0},
        ):
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[call]))

    monkeypatch.setattr(NormalizedChatAnthropic, "_stream", chunks)
    llm = AnthropicClient(model="claude-sonnet-5", api_key="test", streaming=True).get_llm()
    assert llm.with_structured_output(Rating).invoke("Analyze").rating == "Hold"


def test_gateway_dictionary_metadata_does_not_discard_completed_stream():
    event = RawMessageDeltaEvent.model_validate({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 10},
        "context_management": {"applied_edits": []},
    })
    llm = AnthropicClient(model="claude-sonnet-5", api_key="test", streaming=True).get_llm()
    chunk, _ = llm._make_message_chunk_from_anthropic_event(event, coerce_content_to_string=True)
    assert chunk.response_metadata["context_management"] == {"applied_edits": []}
    assert chunk.response_metadata["stop_reason"] == "end_turn"
    assert isinstance(event.context_management, dict)


@pytest.mark.parametrize("code,succeeds,expected_calls", [
    ("first_output_timeout", True, 2),
    ("first_output_timeout", False, 3),
    ("authentication_error", False, 1),
])
def test_stream_timeout_retries_are_specific_bounded_and_return_complete_result(
    monkeypatch, code, succeeds, expected_calls
):
    from tradingagents.llm_clients import anthropic_client

    calls = []

    def invoke(self, input, config=None, **kwargs):
        calls.append(input)
        if succeeds and len(calls) == 2:
            return AIMessage(content=[{"type": "text", "text": "Complete report"}])
        response = httpx.Response(200, request=httpx.Request("POST", "https://model.invalid/messages"))
        raise APIStatusError("Gateway error", response=response, body={"error": {"code": code}})

    monkeypatch.setattr(ChatAnthropic, "invoke", invoke)
    monkeypatch.setattr(anthropic_client.time, "sleep", lambda seconds: None)
    llm = AnthropicClient(
        model="claude-sonnet-5", api_key="test", streaming=True, max_retries=2
    ).get_llm()
    if succeeds:
        assert llm.invoke("original evidence").content == "Complete report"
    else:
        with pytest.raises(APIStatusError):
            llm.invoke("original evidence")
    assert calls == ["original evidence"] * expected_calls

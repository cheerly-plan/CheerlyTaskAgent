from __future__ import annotations

import json

import httpx
import pytest

from taskagent.llm import (
    AgentLLM,
    AgentLLMError,
    AgentModel,
    chat_completions_url,
    resolve_agent_model,
)


def _model(**overrides) -> AgentModel:
    values = {
        "id": "qwen-max",
        "label": "Qwen Max",
        "provider": "Qwen",
        "base_url": "https://example.test/v1",
        "api_key": "sk-test",
    }
    values.update(overrides)
    return AgentModel(**values)


async def test_complete_posts_openai_payload_and_returns_text():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "  hello  "}}]})

    llm = AgentLLM(_model(), transport=httpx.MockTransport(handler))
    text = await llm.complete("hi", system="sys")

    assert text == "hello"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "qwen-max"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_chat_completions_url_accepts_full_endpoint():
    assert (
        chat_completions_url("https://example.test/v1/chat/completions/")
        == "https://example.test/v1/chat/completions"
    )


async def test_complete_rejects_missing_configuration():
    with pytest.raises(AgentLLMError, match="Base URL"):
        await AgentLLM(_model(base_url="")).complete("hi")
    with pytest.raises(AgentLLMError, match="API Key"):
        await AgentLLM(_model(api_key="")).complete("hi")


async def test_complete_surfaces_http_error_without_leaking_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    llm = AgentLLM(_model(), transport=httpx.MockTransport(handler))
    with pytest.raises(AgentLLMError) as info:
        await llm.complete("hi")
    assert "401" in str(info.value)
    assert "sk-test" not in str(info.value)


async def test_complete_rejects_empty_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    llm = AgentLLM(_model(), transport=httpx.MockTransport(handler))
    with pytest.raises(AgentLLMError, match="为空"):
        await llm.complete("hi")


def test_resolve_agent_model_prefers_selection_then_first_enabled():
    models = [
        {"id": "disabled", "enabled": False, "base_url": "x", "api_key": "k"},
        {"id": "first", "label": "First", "enabled": True, "base_url": "x", "api_key": "k"},
        {"id": "second", "enabled": True, "base_url": "x", "api_key": "k"},
    ]
    assert resolve_agent_model(models).id == "first"
    assert resolve_agent_model(models, "second").id == "second"
    assert resolve_agent_model(models, "missing").id == "first"
    assert resolve_agent_model(models, "").display == "First"
    assert resolve_agent_model([]) is None
    assert resolve_agent_model([{"id": "x", "enabled": False}]) is None
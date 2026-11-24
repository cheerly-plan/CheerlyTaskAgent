"""Backend-agent LLM client (OpenAI-compatible ``/chat/completions``).

This is deliberately separate from the execution backends: codebuddy / DSH
CLIs are the "hands" that edit code, while the backend agent uses this client
for its own thinking — picking a repository among candidates today, report
summaries / reviews later. The endpoint, model and key come from the UI
"模型配置" page (``data/settings.json``), never from the CLI profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .usage import TokenUsage, extract_llm_usage


class AgentLLMError(RuntimeError):
    """The backend agent could not get a usable answer from its LLM."""


@dataclass(frozen=True)
class AgentModel:
    """One enabled model from the UI model configuration."""

    id: str
    label: str = ""
    provider: str = ""
    base_url: str = ""
    api_key: str = ""

    @property
    def display(self) -> str:
        name = self.label or self.id
        return f"{name} · {self.provider}" if self.provider else name


def resolve_agent_model(
    models: list[dict[str, Any]] | None, agent_model: str = ""
) -> AgentModel | None:
    """Pick the enabled model the backend agent should call.

    ``agent_model`` names one explicitly; an empty/unknown value falls back to
    the first enabled entry, so a single configured model needs no extra step.
    """
    enabled = [
        item
        for item in models or []
        if isinstance(item, dict)
        and item.get("enabled", True)
        and str(item.get("id") or "").strip()
    ]
    chosen: dict[str, Any] | None = None
    if agent_model:
        chosen = next(
            (item for item in enabled if str(item.get("id") or "").strip() == agent_model),
            None,
        )
    chosen = chosen or (enabled[0] if enabled else None)
    if chosen is None:
        return None
    return AgentModel(
        id=str(chosen.get("id") or "").strip(),
        label=str(chosen.get("label") or "").strip(),
        provider=str(chosen.get("provider") or "").strip(),
        base_url=str(chosen.get("base_url") or "").strip(),
        api_key=str(chosen.get("api_key") or "").strip(),
    )


def chat_completions_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _message_text(payload: dict[str, Any]) -> str:
    """Extract assistant text from an OpenAI-compatible response."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        # Some gateways return content parts instead of a plain string.
        parts = [
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        ]
        return "".join(parts).strip()
    return ""


class AgentLLM:
    """Minimal chat client for the backend agent's configured model."""

    def __init__(
        self,
        model: AgentModel,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._transport = transport
        # 最近一次调用的 token 用量；调度台据此记运行账本。
        self.last_usage = TokenUsage()

    def _request(self) -> tuple[str, dict[str, str]]:
        if not self.model.base_url:
            raise AgentLLMError(f"模型 {self.model.display} 未配置 Base URL")
        if not self.model.api_key:
            raise AgentLLMError(f"模型 {self.model.display} 未配置 API Key")
        url = chat_completions_url(self.model.base_url)
        if not url.startswith(("http://", "https://")):
            raise AgentLLMError("Base URL 需要以 http:// 或 https:// 开头")
        headers = {
            "Authorization": f"Bearer {self.model.api_key}",
            "Content-Type": "application/json",
        }
        return url, headers

    async def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> str:
        """Send one chat turn and return the assistant text.

        Raises :class:`AgentLLMError` for missing configuration, transport
        failures, non-2xx responses and empty/malformed answers, so callers can
        decide whether to degrade (planner) or surface the error (API).
        """
        url, headers = self._request()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model.id,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s, transport=self._transport
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as error:
            raise AgentLLMError(f"请求 LLM 失败：{error}") from error
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:300]
            raise AgentLLMError(f"LLM 返回 HTTP {response.status_code}：{detail}")
        try:
            body = response.json()
        except ValueError as error:
            raise AgentLLMError("LLM 返回的不是合法 JSON") from error
        if not isinstance(body, dict):
            raise AgentLLMError("LLM 返回结构异常")
        self.last_usage = extract_llm_usage(body)
        text = _message_text(body)
        if not text:
            raise AgentLLMError("LLM 返回内容为空")
        return text
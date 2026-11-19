from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.errors import AINotConfiguredError, AIServiceError, RateLimitedError
from app.services.ai_service import FALLBACK_BETA, ClaudeProvider

pytestmark = pytest.mark.anyio

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def message(text: str | None = '{"ok": true}', stop_reason: str = "end_turn") -> SimpleNamespace:
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.calls: list[dict] = []
        self.result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def provider(result: Any, model: str = "claude-opus-5-5") -> tuple[ClaudeProvider, FakeClient]:
    client = FakeClient(result)
    return ClaudeProvider("key", model=model, effort="medium", client=client), client


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


async def test_sends_schema_constrained_request_with_fallbacks() -> None:
    p, client = provider(message())
    assert await p.generate_json(system="sys", prompt="hello", schema=SCHEMA) == {"ok": True}
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == "sys"
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "thinking" not in call and "temperature" not in call


async def test_no_fallbacks_for_models_without_support() -> None:
    p, client = provider(message(), model="claude-haiku-5-5")
    await p.generate_json(system="s", prompt="p", schema=SCHEMA)
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


@pytest.mark.parametrize(
    ("result", "error", "needle"),
    [
        (message(stop_reason="refusal"), AIServiceError, "declined"),
        (message(stop_reason="max_tokens"), AIServiceError, "cut off"),
        (message(text=None), AIServiceError, "empty"),
        (message(text="not json"), AIServiceError, "malformed"),
        (message(text="[1, 2]"), AIServiceError, "unexpected shape"),
        (anthropic.AuthenticationError("bad key", response=_response(401), body=None), AINotConfiguredError, "API key"),
        (anthropic.PermissionDeniedError("nope", response=_response(403), body=None), AINotConfiguredError, "not allowed"),
        (anthropic.RateLimitError("slow", response=_response(429), body=None), RateLimitedError, "busy"),
        (anthropic.InternalServerError("boom", response=_response(500), body=None), AIServiceError, "(500)"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), AIServiceError, "too long"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), AIServiceError, "Couldn't reach"),
    ],
)
async def test_failures_become_user_facing_errors(result: Any, error: type[Exception], needle: str) -> None:
    p, _ = provider(result)
    with pytest.raises(error) as excinfo:
        await p.generate_json(system="s", prompt="p", schema=SCHEMA)
    assert needle in str(excinfo.value)

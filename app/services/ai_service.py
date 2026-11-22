"""The AI service abstraction.

The rest of the application only uses `AIProvider.generate_json`: it sends a
system prompt, a user prompt and a JSON schema, and gets back a dict that
matches the schema. Switching providers means writing another class with the
same method; nothing else changes.

`ClaudeProvider` implements it with the Claude API (official `anthropic` SDK)
using schema-constrained output (`output_config.format`), so responses are
always parseable JSON. They are still validated by the callers, because a
schema cannot express rules such as "only reference songs from this list".
"""

import json
from typing import Any, Protocol

import anthropic

from app.errors import AINotConfiguredError, AIServiceError, RateLimitedError

#: Models that support server-side refusal fallbacks (beta). When a request
#: is declined by a safety classifier, the API retries it on a fallback model
#: within the same call instead of returning nothing.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIProvider(Protocol):
    name: str
    model: str

    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000) -> dict:
        """Returns a JSON object matching `schema`, or raises AIServiceError."""
        ...


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        timeout_seconds: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=1)

    async def generate_json(self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000) -> dict:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self._effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if self.model in FALLBACK_MODELS:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"

        try:
            response = await self._client.beta.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise AINotConfiguredError("The AI service rejected the server's API key.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AINotConfiguredError("The server's API key is not allowed to use the AI model.") from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitedError("The AI service is busy. Please wait a moment and try again.") from exc
        except anthropic.APITimeoutError as exc:
            raise AIServiceError("The AI service took too long to respond. Please try again.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIServiceError("Couldn't reach the AI service. Check the internet connection and try again.") from exc
        except anthropic.APIStatusError as exc:
            raise AIServiceError(f"The AI service returned an error ({exc.status_code}). Please try again later.") from exc

        if response.stop_reason == "refusal":
            raise AIServiceError("The AI service declined to evaluate these songs.")
        if response.stop_reason == "max_tokens":
            raise AIServiceError("The AI response was cut off before it was complete. Please try again.")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            raise AIServiceError("The AI service returned an empty response.")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AIServiceError("The AI service returned malformed data.") from exc
        if not isinstance(data, dict):
            raise AIServiceError("The AI service returned data in an unexpected shape.")
        return data

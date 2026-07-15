"""
services/llm/llm_client.py

M09 AI Chat Module — LLMClient.

Thin provider-agnostic wrapper. MVP targets Anthropic's Claude API (with
OpenAI as an alternate provider), routed via `llm_provider` in Settings.

TODO(Phase 2): Move to LangChain's ChatModel abstraction (per SDS tech
stack: "LLM: LangChain 0.1+") once multi-provider fallback, streaming, and
RAG retrieval (pgvector) are needed. MVP calls the provider SDK directly
for a smaller dependency surface.
"""

from __future__ import annotations

import json
import logging

from services.llm.models import AIRequest

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the AI assistant embedded in OpenBIM Studio, a parametric BIM \
authoring tool. Given the user's natural-language request and the current \
model context (spatial tree + selected entity), respond with a single JSON \
object matching this shape:

{
  "intent": "create_component" | "edit_property" | "query" | "explain",
  "commands": [{"type": "CreateComponent" | "EditProperty" | "UpdateSelection", "params": {...}}],
  "text": "human-readable response",
  "suggestions": ["optional next-action suggestions"]
}

Respond with ONLY the JSON object, no surrounding prose.
"""


class LLMClientError(RuntimeError):
    """Raised when the LLM provider call fails or returns no usable content."""


class LLMClient:
    """
    Calls the configured LLM provider and returns raw text for
    OutputParser to validate against the AIResponse schema.
    """

    def __init__(self, provider: str, api_key: str | None) -> None:
        self._provider = provider
        self._api_key = api_key

    async def complete(self, request: AIRequest) -> str:
        """
        Send `request` to the configured LLM provider and return the raw
        text response (expected to be a JSON string per SYSTEM_PROMPT).

        Raises:
            LLMClientError: on missing API key or provider failure.

        TODO: Implement the actual provider call:
            - anthropic: `anthropic.AsyncAnthropic(api_key=...).messages.create(...)`
            - openai:    `openai.AsyncOpenAI(api_key=...).chat.completions.create(...)`
        Both should pass SYSTEM_PROMPT + json.dumps(request.model_context)
        + request.prompt, and enable streaming for WS-based UIs in Phase 2.
        """
        if not self._api_key:
            raise LLMClientError(
                f"No API key configured for provider '{self._provider}'. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in backend/.env."
            )

        context_json = json.dumps(request.model_context.model_dump(mode="json"))
        logger.debug(
            "LLMClient.complete provider=%s prompt=%r context_len=%d",
            self._provider,
            request.prompt,
            len(context_json),
        )

        # TODO: replace with a real provider call.
        raise NotImplementedError("LLMClient.complete: provider integration pending")

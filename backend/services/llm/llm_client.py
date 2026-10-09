"""
services/llm/llm_client.py

M09 AI Chat Module — LLMClient.

Thin provider-agnostic wrapper. MVP targets Anthropic's Claude API (with
OpenAI as an alternate provider), routed via `llm_provider` in Settings.

Day-29: "ollama" is the first provider actually called -- a local model
over Ollama's HTTP API (POST /api/chat), with the reply constrained by a
JSON schema to the Day-27 EditProperty shape. The schema is syntactic
control only; AIChatService still validates every command against
services/commands/contract.py, the semantic boundary.

TODO(Phase 2): Move to LangChain's ChatModel abstraction (per SDS tech
stack: "LLM: LangChain 0.1+") once multi-provider fallback, streaming, and
RAG retrieval (pgvector) are needed. MVP calls the provider SDK directly
for a smaller dependency surface.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

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

# Day-29: task prompt for the Ollama path, narrowed to the Day-27 command
# contract. It guides the model only; nothing here is trusted for safety.
EDIT_PROPERTY_SYSTEM_PROMPT = """\
You turn a user's request about a building model into a JSON proposal for \
OpenBIM Studio. You never apply changes; you only propose them.

The ONLY supported operation is changing one dimension of one existing wall \
(IfcWall): Qto_WallBaseQuantities.Length, Qto_WallBaseQuantities.Height or \
Qto_WallBaseQuantities.Thickness, to an explicit number in "mm" or "m".

Rules:
- target_guid must be the guid of the selected wall from the model context. \
Never invent a guid. If no wall is selected, return no command.
- Copy the number and unit the user gave ("metres"/"meters" means "m", \
"millimetres"/"millimeters" means "mm"). Do not convert or calculate values. \
If the user gives any other unit, return no command.
- If the user gives no explicit number (for example "make it longer"), \
return no command.
- Everything else is unsupported and gets no command: creating, deleting, \
moving, rotating or copying elements; doors, windows, columns, beams, \
slabs, MEP; any other property; OpenSCAD or any code.

Reply with JSON only:
- a supported request: {"intent": "edit_property", "commands": [one \
EditProperty command], "text": a short description of the proposed change}
- anything else: {"intent": "query", "commands": [], "text": a short \
explanation of why no change can be proposed}
"""

# Ollama `format` JSON schema mirroring the Day-27 EditProperty contract
# (services/commands/contract.py; tests check the enums stay in sync).
EDIT_PROPERTY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["edit_property", "query"]},
        "commands": {
            "type": "array",
            "maxItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["EditProperty"]},
                    "params": {
                        "type": "object",
                        "properties": {
                            "target_guid": {"type": "string"},
                            "pset": {"type": "string", "enum": ["Qto_WallBaseQuantities"]},
                            "property": {"type": "string", "enum": ["Length", "Height", "Thickness"]},
                            "value": {"type": "number"},
                            "unit": {"type": "string", "enum": ["mm", "m"]},
                        },
                        "required": ["target_guid", "pset", "property", "value", "unit"],
                        "additionalProperties": False,
                    },
                },
                "required": ["type", "params"],
                "additionalProperties": False,
            },
        },
        "text": {"type": "string"},
    },
    "required": ["intent", "commands", "text"],
    "additionalProperties": False,
}


class LLMClientError(RuntimeError):
    """Raised when the LLM provider call fails or returns no usable content."""


class LLMClient:
    """
    Calls the configured LLM provider and returns raw text for
    OutputParser to validate against the AIResponse schema.
    """

    def __init__(
        self,
        provider: str,
        api_key: str | None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        num_ctx: int = 8192,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        # Day-29: Ollama settings (unused by other providers). `transport`
        # lets tests replace the HTTP layer; None means real network I/O.
        self._base_url = base_url
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._num_ctx = num_ctx
        self._transport = transport

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
        if self._provider == "ollama":
            return await self._complete_ollama(request)

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

    async def _complete_ollama(self, request: AIRequest) -> str:
        """
        Day-29: one non-streaming POST /api/chat to a local Ollama server.
        Every failure becomes LLMClientError, so AIChatService's graceful
        error reply applies.
        """
        if not self._base_url or not self._model:
            raise LLMClientError("Ollama base URL and model must both be configured.")

        context_json = json.dumps(request.model_context.model_dump(mode="json"))
        payload = {
            "model": self._model,
            "stream": False,
            "format": EDIT_PROPERTY_RESPONSE_SCHEMA,
            "options": {"temperature": 0, "num_ctx": self._num_ctx},
            "messages": [
                {"role": "system", "content": EDIT_PROPERTY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Model context (JSON):\n{context_json}\n\n"
                        f"Selected guid: {request.model_context.selected_guid or 'none'}\n\n"
                        f"User request: {request.prompt}"
                    ),
                },
            ],
        }

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"Ollama did not respond within {self._timeout_seconds:g} s") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"Could not reach Ollama at {self._base_url}: {exc}") from exc

        if response.status_code != 200:
            raise LLMClientError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMClientError("Ollama returned a response that is not JSON") from exc

        message = body.get("message") if isinstance(body, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMClientError("Ollama returned an empty response")

        # A prompt that fills the window may have been truncated by Ollama.
        prompt_tokens = body.get("prompt_eval_count")
        if isinstance(prompt_tokens, int) and prompt_tokens >= self._num_ctx:
            raise LLMClientError(
                f"Model context too large: the prompt used {prompt_tokens} of {self._num_ctx} tokens"
            )

        logger.info(
            "Ollama model=%s prompt_tokens=%s total_duration_ms=%s",
            self._model,
            prompt_tokens,
            round(body["total_duration"] / 1e6) if isinstance(body.get("total_duration"), int) else None,
        )
        return content

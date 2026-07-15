"""
services/llm/chat_service.py

M09 AI Chat Module — top-level facade, analogous to services/openscad/engine.py.

Orchestrates Flow 2 (SDS Section 8.4):
    ContextBuilder.build(selected_guid)
    -> LLMClient.complete(AIRequest)
    -> OutputParser.parse(raw_text)
    -> AIResponse
"""

from __future__ import annotations

import logging

from services.llm.context_builder import ContextBuilder
from services.llm.llm_client import LLMClient, LLMClientError
from services.llm.models import AIRequest, AIResponse, Intent
from services.llm.output_parser import OutputParser, OutputParseError

logger = logging.getLogger(__name__)


class AIChatService:
    """Facade tying together ContextBuilder, LLMClient, OutputParser."""

    def __init__(
        self,
        context_builder: ContextBuilder,
        llm_client: LLMClient,
        output_parser: OutputParser,
    ) -> None:
        self._context_builder = context_builder
        self._llm_client = llm_client
        self._output_parser = output_parser

    async def send_prompt(self, prompt: str, selected_guid: str | None = None) -> AIResponse:
        """
        Handle a single natural-language prompt end-to-end.

        On any provider/parsing failure, returns a graceful AIResponse with
        intent="query" and an explanatory `text`, matching the SRS
        alternative flow: "LLM API fails -> chat shows error, suggests
        retry" -- rather than propagating a raw exception to the API layer.
        """
        context = self._context_builder.build(selected_guid)
        request = AIRequest(prompt=prompt, model_context=context)

        try:
            raw_text = await self._llm_client.complete(request)
            return self._output_parser.parse(raw_text)
        except (LLMClientError, OutputParseError) as exc:
            logger.warning("AI chat request failed: %s", exc)
            return AIResponse(
                intent=Intent.QUERY,
                commands=[],
                text=f"Sorry, I couldn't process that request: {exc}",
                suggestions=["Try rephrasing your request", "Retry"],
            )

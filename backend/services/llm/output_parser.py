"""
services/llm/output_parser.py

M09 AI Chat Module — OutputParser.

Parses and validates the raw LLM text response into the strongly-typed
AIResponse schema, so malformed/hallucinated output never reaches the
frontend or the Command execution path.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from services.llm.models import AIResponse

logger = logging.getLogger(__name__)


class OutputParseError(ValueError):
    """Raised when the LLM's raw text cannot be parsed into an AIResponse."""


class OutputParser:
    """Validates raw LLM text against the AIResponse JSON Schema."""

    def parse(self, raw_text: str) -> AIResponse:
        """
        Parse `raw_text` (expected to be a JSON object per LLMClient's
        SYSTEM_PROMPT) into a validated AIResponse.

        Raises:
            OutputParseError: if the text is not valid JSON, or does not
            match the AIResponse schema (missing/invalid fields).

        TODO: Add a repair pass (e.g. strip markdown code fences, retry
        with a "fix your JSON" follow-up prompt) before giving up.
        """
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise OutputParseError(f"LLM response was not valid JSON: {exc}") from exc

        try:
            return AIResponse.model_validate(payload)
        except ValidationError as exc:
            raise OutputParseError(f"LLM response did not match AIResponse schema: {exc}") from exc

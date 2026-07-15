"""
services/llm — M09 AI Chat Module.

LLM-powered natural language interface. Accepts free-text, builds context
from current model state, calls the LLM API, parses structured output, and
emits Commands (SDS Section 4, M09).

Public contract:
    send_prompt(text, context) -> AIResponse {commands, text, suggestions}
"""

from services.llm.chat_service import AIChatService
from services.llm.context_builder import ContextBuilder
from services.llm.llm_client import LLMClient
from services.llm.models import AIRequest, AIResponse, Command, ModelContext
from services.llm.output_parser import OutputParser

__all__ = [
    "AIChatService",
    "AIRequest",
    "AIResponse",
    "Command",
    "ContextBuilder",
    "LLMClient",
    "ModelContext",
    "OutputParser",
]

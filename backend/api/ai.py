"""
api/ai.py

POST /api/ai/prompt — natural language -> structured commands.
Backs the M09 AI Chat Module (SDS Flow 2).

TODO(Phase 2): Add a WS /ws/session/{id} endpoint for streaming AI
responses per SDS Section 9.1; MVP uses a single synchronous POST.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_ai_chat_service
from api.schemas import AIPromptRequest, AIResponse
from services.llm.chat_service import AIChatService

router = APIRouter(tags=["ai"])


@router.post("/ai/prompt", response_model=AIResponse)
async def send_prompt(
    body: AIPromptRequest,
    chat_service: AIChatService = Depends(get_ai_chat_service),
) -> AIResponse:
    """
    Send a natural-language prompt to the AI Chat Module and return the
    parsed AIResponse (intent, commands, human-readable text).

    Command *execution* (creating entities, triggering SCAD renders) is
    intentionally left to the caller for MVP: the frontend inspects
    `commands` and issues the corresponding follow-up API calls (e.g. a
    CreateComponent command -> POST /api/scad/render). This keeps M09
    decoupled from M08/M10 side effects until M07 Command Interpreter
    exists to own that orchestration (Phase 2).
    """
    return await chat_service.send_prompt(body.prompt, body.selected_guid)

"""
api/ai.py

POST /api/ai/prompt  — natural language -> validated EditProperty proposals.
POST /api/ai/confirm — Day-31: execute one proposal the user confirmed.
Backs the M09 AI Chat Module (SDS Flow 2).

TODO(Phase 2): Add a WS /ws/session/{id} endpoint for streaming AI
responses per SDS Section 9.1; MVP uses a single synchronous POST.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_ai_chat_service, get_entity_store
# Same rejection -> HTTP mapping as the manual edit path, so an AI edit that
# fails in set_prop() is reported exactly like a failed manual edit.
from api.properties import _REJECTIONS as PROPERTY_EDIT_REJECTIONS
from api.schemas import AIConfirmRequest, AIConfirmResponse, AIPromptRequest, AIResponse, ErrorResponse
from services.commands.contract import CommandRejected
from services.commands.executor import execute_ai_proposal
from services.ifc.entity_store import EntityStore, PropertyEditRejected
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

    Day-29/31: this never executes anything. Returned commands are
    validated EditProperty proposals, each with a `proposal_ids` entry;
    the user applies one explicitly through POST /api/ai/confirm.
    """
    return await chat_service.send_prompt(body.prompt, body.selected_guid)


@router.post(
    "/ai/confirm",
    response_model=AIConfirmResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
               500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def confirm_proposal(
    body: AIConfirmRequest,
    chat_service: AIChatService = Depends(get_ai_chat_service),
    store: EntityStore = Depends(get_entity_store),
) -> AIConfirmResponse:
    """
    Day-31: apply a pending AI proposal after explicit user confirmation.

    The proposal is taken (removed) first, so it executes at most once.
    Every AI check runs again against the current model and the selection
    sent here; only then is execute_command() -> EntityStore.set_prop()
    called (Day-28 atomic edit + deterministic geometry regeneration).

        404  proposal_not_found    unknown, expired (600 s), or already used
        422  <rejection reason>    a check failed now (e.g. target_mismatch,
                                   stale_proposal)
        422/502/500                set_prop() rejected the edit (as POST /api/properties)
    """
    proposal = chat_service.take_proposal(body.proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error="This proposal is no longer available (already used or expired); nothing was changed. "
                      "Ask for a new proposal.",
                code="proposal_not_found",
            ).model_dump(),
        )

    try:
        result = execute_ai_proposal(proposal, body.selected_guid, store)
    except CommandRejected as exc:
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                error="The proposal was rejected; nothing was changed.", detail=exc.detail, code=exc.reason.value
            ).model_dump(),
        ) from exc
    except PropertyEditRejected as exc:
        status_code, error = PROPERTY_EDIT_REJECTIONS[exc.reason]
        raise HTTPException(
            status_code=status_code,
            detail=ErrorResponse(error=error, detail=exc.detail, code=exc.reason.value).model_dump(),
        ) from exc

    edit = result.edit
    return AIConfirmResponse(
        success=True,
        text=f"Applied: set {edit.pset}.{edit.property} of '{edit.target_guid}' to {edit.value_mm} mm.",
        entity=result.entity,
    )

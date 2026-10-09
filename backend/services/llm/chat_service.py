"""
services/llm/chat_service.py

M09 AI Chat Module — top-level facade, analogous to services/openscad/engine.py.

Orchestrates Flow 2 (SDS Section 8.4):
    ContextBuilder.build(selected_guid)
    -> LLMClient.complete(AIRequest)
    -> OutputParser.parse(raw_text)
    -> validate_command() for every command (Day-29)
    -> verify_provenance() against the user's request (Day-30)
    -> verify_property() / verify_target() (Day-30 hardening)
    -> AIResponse

Day-29: the result is a PROPOSAL only. Commands are validated against the
Day-27 contract (services/commands/contract.py) and only accepted
EditProperty commands are returned, normalised to millimetres; anything
else is reported in `text` and dropped, so an LLM can never hand the
frontend a CreateComponent (or any other) command. Nothing here executes
a command, edits the EntityStore or regenerates geometry.
"""

from __future__ import annotations

import logging

from services.ifc.entity_store import EntityNotFoundError, EntityStore
from services.llm.context_builder import ContextBuilder
from services.llm.llm_client import LLMClient, LLMClientError
from services.llm.models import AIRequest, AIResponse, Command, CommandType, Intent
from services.llm.output_parser import OutputParser, OutputParseError

# Module import, not `from ... import validate_command`: contract.py itself
# imports services.llm.models, so names are looked up at call time to keep
# the services.llm <-> services.commands import cycle safe.
from services.commands import contract as command_contract
from services.commands import provenance as command_provenance

logger = logging.getLogger(__name__)


class AIChatService:
    """Facade tying together ContextBuilder, LLMClient, OutputParser and command validation."""

    def __init__(
        self,
        context_builder: ContextBuilder,
        llm_client: LLMClient,
        output_parser: OutputParser,
        entity_store: EntityStore,
    ) -> None:
        self._context_builder = context_builder
        self._llm_client = llm_client
        self._output_parser = output_parser
        # Day-29: read-only use -- validate_command() never mutates the store.
        self._entity_store = entity_store

    async def send_prompt(self, prompt: str, selected_guid: str | None = None) -> AIResponse:
        """
        Handle a single natural-language prompt end-to-end.

        On any provider/parsing failure, returns a graceful AIResponse with
        intent="query" and an explanatory `text`, matching the SRS
        alternative flow: "LLM API fails -> chat shows error, suggests
        retry" -- rather than propagating a raw exception to the API layer.
        """
        try:
            context = self._context_builder.build(selected_guid)
        except EntityNotFoundError:
            # Day-29: a stale selection is reported, not a 500; the LLM is not called.
            return AIResponse(
                intent=Intent.QUERY,
                commands=[],
                text=f"The selected entity '{selected_guid}' no longer exists. No change was proposed.",
                suggestions=["Reselect the wall and retry"],
            )
        request = AIRequest(prompt=prompt, model_context=context)

        try:
            raw_text = await self._llm_client.complete(request)
            parsed = self._output_parser.parse(raw_text)
        except (LLMClientError, OutputParseError) as exc:
            logger.warning("AI chat request failed: %s", exc)
            return AIResponse(
                intent=Intent.QUERY,
                commands=[],
                text=f"Sorry, I couldn't process that request: {exc}",
                suggestions=["Try rephrasing your request", "Retry"],
            )
        return self._to_proposal(parsed, prompt, selected_guid)

    def _to_proposal(self, parsed: AIResponse, prompt: str, selected_guid: str | None) -> AIResponse:
        """
        Day-29: validate every parsed command; keep only accepted ones as
        canonical (millimetre) EditProperty proposals. The response text
        is generated here rather than taken from the LLM whenever commands
        were returned, so it cannot claim a change was applied.
        """
        if not parsed.commands:
            intent = parsed.intent if parsed.intent in (Intent.QUERY, Intent.EXPLAIN) else Intent.QUERY
            return AIResponse(
                intent=intent,
                commands=[],
                text=f"{parsed.text}\nNo change was proposed.".strip(),
                suggestions=parsed.suggestions,
            )

        proposals: list[Command] = []
        lines: list[str] = []
        for command in parsed.commands:
            try:
                edit = command_contract.validate_command(command, self._entity_store)
                # Day-30: the value must be the user's own explicit quantity.
                command_provenance.verify_provenance(edit, prompt)
                # Day-30 hardening: the property and (if referred to) the
                # selected wall must also be the user's own.
                command_provenance.verify_property(edit, prompt)
                command_provenance.verify_target(edit, prompt, selected_guid)
            except command_contract.CommandRejected as exc:
                logger.info("AI command rejected: %s", exc)
                lines.append(f"Rejected {command.type.value}: {exc.reason.value} ({exc.detail}).")
                continue
            proposals.append(
                Command(
                    type=CommandType.EDIT_PROPERTY,
                    params={
                        "target_guid": edit.target_guid,
                        "pset": edit.pset,
                        "property": edit.property,
                        "value": edit.value_mm,
                        "unit": "mm",
                    },
                )
            )
            lines.append(
                f"Proposed (not applied): set {edit.pset}.{edit.property} of "
                f"'{edit.target_guid}' to {edit.value_mm} mm."
            )

        if not proposals:
            lines.append("No change was proposed.")
        return AIResponse(
            intent=Intent.EDIT_PROPERTY if proposals else Intent.QUERY,
            commands=proposals,
            text="\n".join(lines),
            suggestions=[] if proposals else ["Try e.g. 'Make the selected wall 3.2 m high'"],
        )

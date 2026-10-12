"""
services/commands — Day-27 AI Command Contract.

Structured command representation, validation, and a minimal execution
bridge into the existing property pipeline. No LLM integration.

Public contract:
    validate_command(command, store) -> ValidatedPropertyEdit   (raises CommandRejected)
    execute_command(command, store)  -> CommandExecutionResult  (raises CommandRejected)
    verify_provenance(edit, request_text) -> UserQuantity       (Day-30; raises CommandRejected)
    verify_property(edit, request_text) -> str                  (Day-30; raises CommandRejected)
    verify_target(edit, request_text, selected_guid) -> None    (Day-30; raises CommandRejected)
    verify_selected_target(edit, selected_guid) -> None         (Day-31; raises CommandRejected)
    validate_ai_edit(command, store, request_text, selected_guid) -> ValidatedPropertyEdit  (Day-31)
    execute_ai_proposal(proposal, selected_guid, store) -> CommandExecutionResult           (Day-31)
"""

from services.commands.contract import (
    CommandRejected,
    RejectionReason,
    ValidatedPropertyEdit,
    validate_command,
)
from services.commands.ai_edit import PendingProposal, ProposalRegistry, validate_ai_edit
from services.commands.executor import CommandExecutionResult, execute_ai_proposal, execute_command
from services.commands.provenance import (
    UserQuantity,
    verify_property,
    verify_provenance,
    verify_selected_target,
    verify_target,
)

__all__ = [
    "CommandExecutionResult",
    "CommandRejected",
    "PendingProposal",
    "ProposalRegistry",
    "RejectionReason",
    "UserQuantity",
    "ValidatedPropertyEdit",
    "execute_ai_proposal",
    "execute_command",
    "validate_ai_edit",
    "validate_command",
    "verify_property",
    "verify_provenance",
    "verify_selected_target",
    "verify_target",
]

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
"""

from services.commands.contract import (
    CommandRejected,
    RejectionReason,
    ValidatedPropertyEdit,
    validate_command,
)
from services.commands.executor import CommandExecutionResult, execute_command
from services.commands.provenance import UserQuantity, verify_property, verify_provenance, verify_target

__all__ = [
    "CommandExecutionResult",
    "CommandRejected",
    "RejectionReason",
    "UserQuantity",
    "ValidatedPropertyEdit",
    "execute_command",
    "validate_command",
    "verify_property",
    "verify_provenance",
    "verify_target",
]

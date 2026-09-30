"""
services/commands — Day-27 AI Command Contract.

Structured command representation, validation, and a minimal execution
bridge into the existing property pipeline. No LLM integration.

Public contract:
    validate_command(command, store) -> ValidatedPropertyEdit   (raises CommandRejected)
    execute_command(command, store)  -> CommandExecutionResult  (raises CommandRejected)
"""

from services.commands.contract import (
    CommandRejected,
    RejectionReason,
    ValidatedPropertyEdit,
    validate_command,
)
from services.commands.executor import CommandExecutionResult, execute_command

__all__ = [
    "CommandExecutionResult",
    "CommandRejected",
    "RejectionReason",
    "ValidatedPropertyEdit",
    "execute_command",
    "validate_command",
]

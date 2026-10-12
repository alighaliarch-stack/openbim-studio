"""
services/commands/ai_edit.py

Day-31 AI -> property bridge: the deterministic gate for AI-originated
edits, and the single-use store of pending proposals.

validate_ai_edit() is the ONE ordered list of checks an AI EditProperty
must pass. AIChatService runs it when proposing, and
executor.execute_ai_proposal() runs it again, against the current model
and selection, immediately before executing:

    validate_command()        Day-27 contract (existing IfcWall, Length/Height/Thickness, mm|m, bounds)
    verify_provenance()       Day-30 value/unit is the user's explicit quantity
    verify_property()         Day-30 property is the one the user's wording names
    verify_selected_target()  Day-31 every AI edit targets the selected wall
                              (before verify_target, so "nothing selected"
                              is reported as no_selection)
    verify_target()           Day-30 "the selected wall" means the selected GUID
                              (kept; implied by the Day-31 check)

and, at confirmation only, verify_unchanged(): the target property still
has the value it had when the proposal was made (stale_proposal otherwise).

ProposalRegistry keeps accepted proposals server-side under a random id,
so a confirmation can only refer to a proposal the server validated; it
cannot supply its own command or value. take() removes the proposal, so
each one can be executed at most once, and returns nothing for a proposal
older than PROPOSAL_TTL_SECONDS.

Nothing here executes a command or mutates the model (reads only).
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
# Module-level name, looked up at call time, so tests can control the clock
# without patching time.monotonic for the whole process.
from time import monotonic
from typing import TYPE_CHECKING, Any

# Module imports, looked up at call time, for the same import-cycle reason
# as chat_service.py and provenance.py.
from services.commands import contract, provenance

if TYPE_CHECKING:
    from services.ifc.entity_store import EntityStore
    from services.llm.models import Command

# Pending proposals kept at most; the oldest is dropped first. Bounds memory
# for proposals that are never confirmed (single-user MVP).
MAX_PENDING_PROPOSALS = 32

# Day-31: a proposal can be confirmed for at most this long after it was
# made (server-enforced; the UI's Dismiss is visual only).
PROPOSAL_TTL_SECONDS = 600.0


def validate_ai_edit(
    command: Command, store: EntityStore, request_text: str, selected_guid: str | None
) -> contract.ValidatedPropertyEdit:
    """
    Run every Day-27/30/31 check on an AI-originated command, in order.

    Raises:
        CommandRejected: with the first failing check's reason.
    """
    edit = contract.validate_command(command, store)
    provenance.verify_provenance(edit, request_text)
    provenance.verify_property(edit, request_text)
    provenance.verify_selected_target(edit, selected_guid)
    provenance.verify_target(edit, request_text, selected_guid)
    return edit


def current_value(store: EntityStore, edit: contract.ValidatedPropertyEdit) -> Any:
    """The target property's value as currently stored (validate_command() ensured it exists)."""
    return store.get_entity(edit.target_guid).psets[edit.pset][edit.property]


@dataclass(frozen=True)
class PendingProposal:
    """An accepted, canonical (mm) EditProperty proposal awaiting confirmation."""

    proposal_id: str
    command: Command
    request_text: str  # the user's request, re-checked at confirmation
    selected_guid: str  # the selection the proposal was validated against
    original_value: Any  # the target property's stored value when proposed
    created_at: float  # monotonic() seconds


def verify_unchanged(proposal: PendingProposal, edit: contract.ValidatedPropertyEdit, store: EntityStore) -> None:
    """
    Reject a proposal whose target property changed since it was proposed
    (e.g. by a manual edit), so an old proposal cannot overwrite it.

    Values are compared as stored (no unit or type conversion).

    Raises:
        CommandRejected: STALE_PROPOSAL.
    """
    now = current_value(store, edit)
    if now != proposal.original_value:
        raise contract.CommandRejected(
            contract.RejectionReason.STALE_PROPOSAL,
            f"the wall changed since this proposal was made ({edit.pset}.{edit.property} was "
            f"{proposal.original_value}, is now {now}); ask for a new proposal",
        )


class ProposalRegistry:
    """In-memory, bounded, single-use, time-limited store of pending proposals."""

    def __init__(self, capacity: int = MAX_PENDING_PROPOSALS, ttl_seconds: float = PROPOSAL_TTL_SECONDS) -> None:
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._pending: OrderedDict[str, PendingProposal] = OrderedDict()

    def add(self, command: Command, request_text: str, selected_guid: str, original_value: Any) -> str:
        proposal_id = uuid.uuid4().hex
        self._pending[proposal_id] = PendingProposal(
            proposal_id, command, request_text, selected_guid, original_value, monotonic()
        )
        while len(self._pending) > self._capacity:
            self._pending.popitem(last=False)
        return proposal_id

    def take(self, proposal_id: str) -> PendingProposal | None:
        """Remove and return the proposal, or None if unknown, already taken or expired."""
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None or monotonic() - proposal.created_at >= self._ttl_seconds:
            return None
        return proposal

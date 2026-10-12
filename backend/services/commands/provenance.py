"""
services/commands/provenance.py

Day-30 AI safety boundary -- numeric/unit provenance.

The LLM may interpret intent, but it is not the source of numeric BIM
values or unit conversion. After validate_command() has accepted an
EditProperty proposal, verify_provenance() checks that its value is
traceable to the one explicit quantity the user wrote:

    "Make the wall 3.2 m high"
        -> user quantity 3.2, unit m        (parsed here, deterministic)
        -> canonical 3200 mm                (Decimal x UNIT_TO_MM)
        -> proposal must also be 3200 mm    (else rejected)

So "3.2 m" proposed as 3200 m, or a value invented for "make it longer",
is rejected even though it passes the structural contract. Rules are
deliberately conservative (MVP): exactly one quantity, written as digits
with an mm/m unit, and no relative wording; anything else is rejected
rather than guessed.

Day-30 hardening adds two more deterministic checks:
    verify_property() -- the property named by the user's wording
                         ("high" -> Height) must be the proposed property;
    verify_target()   -- if the user refers to the selected wall, the
                         proposal must target the selected GUID.

Day-31 (AI -> property bridge) adds the MVP target policy:
    verify_selected_target() -- every AI edit, whatever its wording, must
                         target the selected GUID; with no selection it is
                         rejected, never redirected to another wall.

Pure functions: no I/O, no EntityStore access, no execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# Module import, not `from ... import`: contract.py imports services.llm,
# whose chat_service imports this module, so names are looked up at call
# time to keep that import cycle safe (same pattern as chat_service.py).
from services.commands import contract

# A number written with digits (decimal point only) directly followed by a
# millimetre/metre unit. The lookbehind rejects numbers that continue
# another token ("3,2 m" -> no match for "2 m"; "-3 m", "x3 m", "1e3 m").
# Other units (cm, ft, km, ...) are deliberately not recognised.
_QUANTITY = re.compile(
    r"(?<![\w.,+\-])(\d+(?:\.\d+)?|\.\d+)\s*(millimet(?:er|re)s?|met(?:er|re)s?|mm|m)(?!\w)",
    re.IGNORECASE,
)

# Wording that makes a request relative ("2 m longer", "increase by 10%").
# Relative edits are out of MVP scope, so they are rejected rather than
# letting a relative amount be proposed as an absolute dimension.
_RELATIVE = re.compile(
    r"%|\b(?:longer|shorter|taller|higher|lower|thicker|thinner|wider|narrower|"
    r"increase\w*|decrease\w*|reduce\w*|extend\w*|shrink\w*|grow\w*|"
    r"by|more|less|twice|double\w*|half|halve\w*|times|percent)\b",
    re.IGNORECASE,
)

# Explicit wording -> the wall property it names. "longer"/"taller" etc. do
# not match (whole words only); relative wording is rejected anyway.
_PROPERTY_WORDS: dict[str, re.Pattern[str]] = {
    "Height": re.compile(r"\b(?:high|tall|height)\b", re.IGNORECASE),
    "Length": re.compile(r"\b(?:long|length)\b", re.IGNORECASE),
    "Thickness": re.compile(r"\b(?:thick|thickness)\b", re.IGNORECASE),
}

# Wording that refers to the wall currently selected in the viewer.
_SELECTION_REFERENCE = re.compile(r"\b(?:selected|selection|this wall)\b", re.IGNORECASE)


@dataclass(frozen=True)
class UserQuantity:
    """An explicit quantity found in the user's request."""

    text: str  # as written, e.g. "3.2 m"
    value: Decimal
    unit: str  # "mm" | "m"
    value_mm: Decimal  # canonical, computed here -- never by the LLM


def extract_quantities(request_text: str) -> list[UserQuantity]:
    """Return every explicit mm/m quantity in `request_text`, in order."""
    quantities = []
    for match in _QUANTITY.finditer(request_text):
        number, unit_word = match.groups()
        unit = "mm" if unit_word.lower() == "mm" or unit_word.lower().startswith("milli") else "m"
        value = Decimal(number)
        quantities.append(UserQuantity(match.group(0), value, unit, value * contract.UNIT_TO_MM[unit]))
    return quantities


def verify_provenance(edit: contract.ValidatedPropertyEdit, request_text: str) -> UserQuantity:
    """
    Check that an accepted edit's value is the user's own explicit quantity.

    Returns the matching UserQuantity.

    Raises:
        CommandRejected: NO_EXPLICIT_QUANTITY, AMBIGUOUS_QUANTITY,
            RELATIVE_CHANGE_UNSUPPORTED or QUANTITY_MISMATCH.
    """
    Reason = contract.RejectionReason
    quantities = extract_quantities(request_text)

    if not quantities:
        raise contract.CommandRejected(
            Reason.NO_EXPLICIT_QUANTITY,
            f"the request states no explicit dimension in mm or m, so the proposed "
            f"{edit.source_value} {edit.source_unit} cannot be traced to it",
        )
    if len(quantities) > 1:
        raise contract.CommandRejected(
            Reason.AMBIGUOUS_QUANTITY,
            f"the request states {len(quantities)} dimensions "
            f"({', '.join(q.text for q in quantities)}); state exactly one",
        )
    (quantity,) = quantities

    relative = _RELATIVE.search(request_text)
    if relative:
        raise contract.CommandRejected(
            Reason.RELATIVE_CHANGE_UNSUPPORTED,
            f"relative changes ('{relative.group(0)}') are not supported; state the new absolute dimension",
        )

    if Decimal(repr(edit.value_mm)) != quantity.value_mm:
        raise contract.CommandRejected(
            Reason.QUANTITY_MISMATCH,
            f"the request says {quantity.text} (= {quantity.value_mm.normalize():f} mm) but the proposal is "
            f"{edit.source_value} {edit.source_unit} (= {edit.value_mm} mm)",
        )
    return quantity


def verify_property(edit: contract.ValidatedPropertyEdit, request_text: str) -> str:
    """
    Check that an accepted edit's property is the one the user's wording names.

    Returns the property name.

    Raises:
        CommandRejected: NO_EXPLICIT_PROPERTY, AMBIGUOUS_PROPERTY or PROPERTY_MISMATCH.
    """
    Reason = contract.RejectionReason
    named = [prop for prop, pattern in _PROPERTY_WORDS.items() if pattern.search(request_text)]

    if not named:
        raise contract.CommandRejected(
            Reason.NO_EXPLICIT_PROPERTY,
            f"the request does not say which dimension to change (high/long/thick), so the proposed "
            f"{edit.property} cannot be traced to it",
        )
    if len(named) > 1:
        raise contract.CommandRejected(
            Reason.AMBIGUOUS_PROPERTY,
            f"the request names {len(named)} dimensions ({', '.join(named)}); name exactly one",
        )
    (prop,) = named

    if edit.property != prop:
        raise contract.CommandRejected(
            Reason.PROPERTY_MISMATCH,
            f"the request asks for {prop} but the proposal edits {edit.property}",
        )
    return prop


def verify_target(edit: contract.ValidatedPropertyEdit, request_text: str, selected_guid: str | None) -> None:
    """
    If the request refers to the selected wall, check that the edit targets it.

    Requests that do not refer to the selection are not checked here.

    Raises:
        CommandRejected: TARGET_MISMATCH.
    """
    if not _SELECTION_REFERENCE.search(request_text):
        return
    if edit.target_guid != selected_guid:
        selected = f"'{selected_guid}'" if selected_guid else "nothing"
        raise contract.CommandRejected(
            contract.RejectionReason.TARGET_MISMATCH,
            f"the request refers to the selected wall ({selected} is selected) but the proposal "
            f"targets '{edit.target_guid}'",
        )


def verify_selected_target(edit: contract.ValidatedPropertyEdit, selected_guid: str | None) -> None:
    """
    Day-31 MVP target policy: an AI edit must target the selected wall.

    Unlike verify_target(), this applies to every request ("the wall"
    included). `selected_guid` is the selection the application sent with
    the request, never a GUID from the LLM or the prompt text.

    Raises:
        CommandRejected: NO_SELECTION or TARGET_MISMATCH.
    """
    Reason = contract.RejectionReason
    if not selected_guid:
        raise contract.CommandRejected(
            Reason.NO_SELECTION,
            "no wall is selected; select the wall to edit, then ask again",
        )
    if edit.target_guid != selected_guid:
        raise contract.CommandRejected(
            Reason.TARGET_MISMATCH,
            f"the proposal targets '{edit.target_guid}' but the selected wall is '{selected_guid}'",
        )

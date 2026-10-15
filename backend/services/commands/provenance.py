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

Day-34 (target identity) adds:
    verify_entity_reference() -- a request that names a non-wall element
                         type or a GUID other than the target is rejected,
                         so the selected wall never stands in for it.

Day-37 (quantity completeness) adds:
    unaccounted_quantity_material() -- numbers, signs, length units or
                         number words outside the one recognised quantity
                         ("3 200 mm", "3 m 20 cm", "- 2.5 m", "3 m and a
                         quarter"), so a partial parse is never accepted.
                         Conservative: unrelated numbers ("level 2") are
                         rejected too.

Day-37b closes the guard's edge cases: signs after any whitespace and in
more Unicode forms, "minus"/"negative", qualifiers adjacent to the quantity
("~", "<", ">=", "+", "approximately", "at least", ...) and "x2" multipliers.

Day-37c: hyphen-joined wording with a number ("3 m high-and-20") is not an
identifier; qualifiers roughly/around/up to/under/over/"or so" are
associated with the quantity across punctuation (and, after it, the
property word); an ASCII arrow "->" is not a ">" comparison.

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

# Day-37: a sign directly before a quantity, with any whitespace between
# ("- 2.5 m", "minus 2.5 m"); _QUANTITY's lookbehind only catches an ASCII
# +/- touching the number. Day-37b: any amount of whitespace, the written
# signs, and hyphen U+2010, non-breaking hyphen U+2011, minus U+2212, en/em
# dash U+2013/U+2014, small and fullwidth hyphen-minus U+FE63/U+FF0D.
_SIGN_BEFORE = re.compile(
    r"(?:[+\-\u2010\u2011\u2212\u2013\u2014\ufe63\uff0d]|\b(?:minus|negative))\s*$",
    re.IGNORECASE,
)

# Day-37b: a qualifier before a quantity ("~3.2 m", "<= 3.2 m", "at least
# 3.2 m"), so an approximate or bounded value is never taken as exact.
# Day-37c: also roughly/around/up to/under/over, and punctuation may separate
# it from the quantity ("roughly, 3.2 m"). Still only when nothing but
# punctuation separates them: "Tell me about it: make ..." is not checked.
# A ">" right after "-" is an arrow ("Height -> 3.2 m"), not a comparison;
# "=>" stays a comparison (it can be read as >=).
_QUALIFIER_BEFORE = re.compile(
    r"(?:[~\u2248<\u2264\u2265\u00b1]=?|(?<!-)>=?|"
    r"\b(?:approximately|approx\.?|about|roughly|around|up\s+to|under|over|"
    r"at\s+least|at\s+most|more\s+than|less\s+than))(?=\W*$)",
    re.IGNORECASE,
)

# Day-37b: a qualifier directly after a quantity ("3.2 m+", "3.2 m approx.").
# Day-37c: also roughly/around/"or so"/"or under"/"or over", with punctuation
# and the property word allowed in between ("3.2 m high, approximately").
_QUALIFIER_AFTER = re.compile(
    r"\W*(?:(?:high|tall|long|thick|height|length|thickness)\W*)?"
    r"(?:[+\u00b1]|(?:approx(?:imately|\.)?|roughly|around|or\s+so|or\s+(?:under|over))(?!\w))",
    re.IGNORECASE,
)

# Day-37b: a multiplier token ("x2") is not an identifier, though it starts with a letter.
_MULTIPLIER = re.compile(r"x\d+", re.IGNORECASE)

# Day-37c: a hyphenated token with a property or connector word is wording
# ("3 m high-and-20"), not an identifier, so its digits are extra numeric
# material. Identifiers such as "d33-wall-001" or "Wall-07" contain none.
_PROSE_SEGMENT = re.compile(
    r"and|or|plus|with|then|high|tall|height|long|length|thick|thickness|wide|width",
    re.IGNORECASE,
)

# Day-37: a word-like token outside the quantity (hyphenated runs kept
# together, so "d33-wall-001" is one token). One containing a digit is extra
# numeric material unless it looks like an identifier (_is_identifier).
_RESIDUAL_TOKEN = re.compile(r"[\w$]+(?:-[\w$]+)*")

# An IFC GlobalId: 22 characters of its base-64 alphabet.
_IFC_GLOBAL_ID = re.compile(r"[0-9A-Za-z_$]{22}")

# Day-37: length-unit words outside the quantity, supported or not. Not
# preceded/followed by a word character or apostrophe ("I'm" is no unit).
_UNIT_WORD = re.compile(
    r"(?<![\w'\u2019])(?:mm|m|cm|dm|km|(?:milli|centi|deci|kilo)?met(?:er|re)s?|"
    r"ft|feet|foot|inch(?:es)?|yd|yds|yards?|miles?)(?![\w'\u2019])",
    re.IGNORECASE,
)

# Day-37: number words and fractions that could change or qualify the
# quantity ("3 m and twenty centimetres", "3 m and a quarter"). Not a
# number parser: any such word is rejected. "one" is left out ("this one").
_NUMBER_WORD = re.compile(
    r"\b(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"(?:thir|four|fif|six|seven|eigh|nine)teen|(?:twen|thir|for|fif|six|seven|eigh|nine)ty|"
    r"hundred|thousand|million|dozen|point|half|halves|quarters?|thirds?|"
    r"(?:four|fif|six|seven|eigh|nin|ten)ths?)\b|[\u00bc-\u00be\u2150-\u215e]",
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

# Day-34: element types the user can name that are not walls (whole words,
# or any IFC class name other than IfcWall/IfcWallStandardCase). The MVP
# edits walls only, so a request naming one is about a different element.
_OTHER_ELEMENT_TYPE = re.compile(
    r"\b(?:doors?|windows?|columns?|slabs?|beams?|roofs?|stairs?|staircases?|ceilings?|railings?|"
    r"Ifc(?!Wall(?:StandardCase)?\b)[a-z]+)\b",
    re.IGNORECASE,
)


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


def _is_identifier(token: str) -> bool:
    """A token with digits that names something ("d33-wall-001", "Ifc2x3"), not a number."""
    if re.search(r"[^\W\d]|\$", token) is None or _MULTIPLIER.fullmatch(token):
        return False  # digits only, or "x2": a number
    if "-" in token and any(_PROSE_SEGMENT.fullmatch(part) for part in token.split("-")):
        return False  # "high-and-20": hyphen-joined wording with a number
    # "20cm", "3rd", "1e3" start with a digit and are rejected as numbers.
    return not token[0].isdigit() or "-" in token or _IFC_GLOBAL_ID.fullmatch(token) is not None


def unaccounted_quantity_material(request_text: str) -> list[str]:
    """
    Day-37: return quantity-like material outside the recognised quantities.

    That is: a sign or qualifier directly before a quantity, a qualifier
    directly after it (Day-37b; Day-37c: across punctuation), numeric tokens
    (except identifiers such as GUIDs), length-unit words and number words
    or fractions. Empty means
    every number and unit in the request is part of a quantity
    extract_quantities() found, so none of it is ignored.
    """
    found = []
    residual = request_text
    for match in _QUANTITY.finditer(request_text):
        start, end = match.span()
        for adjacent in (_SIGN_BEFORE.search(request_text, 0, start),
                         _QUALIFIER_BEFORE.search(request_text, 0, start),
                         _QUALIFIER_AFTER.match(request_text, end)):
            if adjacent:
                found.append(adjacent.group(0).strip())
        residual = residual[:start] + " " * (end - start) + residual[end:]

    hits = [
        (m.start(), m.group(0)) for m in _RESIDUAL_TOKEN.finditer(residual)
        if re.search(r"\d", m.group(0)) and not _is_identifier(m.group(0))
    ]
    hits += [(m.start(), m.group(0)) for m in _UNIT_WORD.finditer(residual)]
    hits += [(m.start(), m.group(0)) for m in _NUMBER_WORD.finditer(residual)]
    return found + [text for _, text in sorted(hits)]


def verify_provenance(edit: contract.ValidatedPropertyEdit, request_text: str) -> UserQuantity:
    """
    Check that an accepted edit's value is the user's own explicit quantity.

    Returns the matching UserQuantity.

    Raises:
        CommandRejected: NO_EXPLICIT_QUANTITY, AMBIGUOUS_QUANTITY (also for
            unaccounted quantity material, Day-37), RELATIVE_CHANGE_UNSUPPORTED
            or QUANTITY_MISMATCH.
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

    # Day-37: the one quantity must be the whole of the request's numeric
    # wording, so "3 200 mm" is never accepted as 200 mm (nor "- 2.5 m" as 2.5 m).
    unaccounted = unaccounted_quantity_material(request_text)
    if unaccounted:
        raise contract.CommandRejected(
            Reason.AMBIGUOUS_QUANTITY,
            f"the request contains {', '.join(repr(t) for t in unaccounted)} besides {quantity.text}; "
            f"state exactly one dimension, as digits in mm or m",
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


def verify_entity_reference(
    edit: contract.ValidatedPropertyEdit, request_text: str, known_guids: list[str]
) -> None:
    """
    Day-34: check that the request does not name an element other than the target.

    verify_selected_target() compares only the LLM's target with the
    selection, so a model that silently replaces the element the user named
    with the selected wall would pass it. This reads the user's own words:
    a request naming a non-wall element type ("the door", "IfcWindow"), or
    an existing GUID other than the target, is rejected, never redirected.
    Conservative: any such mention is rejected, even as a location ("the
    wall next to the door"). Names (e.g. "South Wall") are not matched.

    Raises:
        CommandRejected: UNSUPPORTED_ENTITY_REFERENCE or CONFLICTING_TARGET_REFERENCE.
    """
    Reason = contract.RejectionReason
    other_type = _OTHER_ELEMENT_TYPE.search(request_text)
    if other_type:
        raise contract.CommandRejected(
            Reason.UNSUPPORTED_ENTITY_REFERENCE,
            f"the request refers to '{other_type.group(0)}', but only the selected wall can be edited",
        )
    others = sorted(
        guid for guid in known_guids
        if guid != edit.target_guid
        and re.search(rf"(?<![\w-]){re.escape(guid)}(?![\w-])", request_text, re.IGNORECASE)
    )
    if others:
        raise contract.CommandRejected(
            Reason.CONFLICTING_TARGET_REFERENCE,
            f"the request names {', '.join(repr(g) for g in others)} but the proposal targets "
            f"'{edit.target_guid}'; select the element you mean, then ask again",
        )

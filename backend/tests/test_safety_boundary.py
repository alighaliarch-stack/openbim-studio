"""
tests/test_safety_boundary.py

Day-30 AI safety boundary tests: numeric/unit provenance.

An EditProperty proposal is accepted only if, after the Day-27 contract,
its value equals the one explicit quantity in the user's request, as
converted to millimetres by the application (never by the LLM).

- extract_quantities()/verify_provenance() are tested directly (pure).
- The full proposal path (real AIChatService, LLMClient with Ollama
  replaced by httpx.MockTransport, contract, EntityStore on a temporary
  model) is tested with the Day-30 required cases, with spies proving no
  execution, property edit or OpenSCAD render happens.

Needs neither Ollama nor OpenSCAD. backend/data/model.json is never read
or written.

Run from backend/:
    venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.persistence import ModelPersistence  # noqa: E402
from services.commands import ai_edit, executor, provenance  # noqa: E402
from services.commands.contract import CommandRejected, RejectionReason, ValidatedPropertyEdit  # noqa: E402
from services.ifc.entity_store import EntityStore  # noqa: E402
from services.llm.models import Command, CommandType, Intent  # noqa: E402
from services.openscad.engine import OpenSCADEngine  # noqa: E402
from tests.test_llm_intent_parsing import (  # noqa: E402
    QTO,
    STOREY,
    WALL,
    _ProposalTestCase,
    edit_params,
    llm_reply,
    replying,
)

R = RejectionReason

# A second existing wall, so a proposal can target a valid wall that is not the selected one.
OTHER_WALL = "day30-wall-002"


def proposal(prop: str, value: object, unit: str, guid: str = WALL) -> str:
    return llm_reply({"type": "EditProperty", "params": edit_params(prop, value, unit, guid)})


def accepted_edit(value_mm, source_value, source_unit: str, prop: str = "Height",
                  guid: str = WALL) -> ValidatedPropertyEdit:
    return ValidatedPropertyEdit(target_guid=guid, pset=QTO, property=prop, value_mm=value_mm,
                                 source_value=source_value, source_unit=source_unit)


class ExtractQuantitiesTest(unittest.TestCase):
    """Deterministic parsing of explicit mm/m quantities from the request."""

    def test_recognised_quantities(self) -> None:
        cases = [
            ("Make the wall 3.2 m high", "3.2", "m", "3200"),
            ("Make the wall 4 metres long", "4", "m", "4000"),
            ("Make the wall 4 meters long", "4", "m", "4000"),
            ("Make the wall 1 metre long", "1", "m", "1000"),
            ("Make the wall 250 mm thick", "250", "mm", "250"),
            ("Make the wall 250mm thick", "250", "mm", "250"),
            ("Make the wall 0.05 m thick", "0.05", "m", "50"),
            ("Make the wall .5 m thick", ".5", "m", "500"),
            ("Make the wall 180 millimetres thick", "180", "mm", "180"),
            ("Make the wall 180 Millimeters thick", "180", "mm", "180"),
            ("Make the wall 3.2M high", "3.2", "m", "3200"),
            ("Height: 2.75 m.", "2.75", "m", "2750"),
        ]
        for text, value, unit, mm in cases:
            with self.subTest(text):
                (q,) = provenance.extract_quantities(text)
                self.assertEqual((q.value, q.unit, q.value_mm), (Decimal(value), unit, Decimal(mm)))

    def test_not_recognised(self) -> None:
        for text in [
            "Make the wall longer",
            "Make the wall three metres high",  # number words
            "Make the wall 30 cm thick",  # unsupported unit
            "Make the wall 10 ft long",
            "Make the wall 2 km long",
            "Make the wall 10 inches thick",
            "Make the wall 3,2 m high",  # comma decimal: not split into "2 m"
            "Make the wall 1,000 mm long",
            "Make the wall -3 m high",
            "Make the wall 1e3 mm long",
            "Make the wall 3 min",
            "Make the wall 3 high",  # no unit
            "Select wall-001 m",
        ]:
            with self.subTest(text):
                self.assertEqual(provenance.extract_quantities(text), [])

    def test_multiple_quantities_are_all_found(self) -> None:
        found = provenance.extract_quantities("Make it 3 m high and 4000 mm long")
        self.assertEqual([q.value_mm for q in found], [Decimal(3000), Decimal(4000)])


class UnaccountedQuantityMaterialTest(unittest.TestCase):
    """Day-37 unaccounted_quantity_material(): nothing quantity-like outside the one quantity."""

    def test_extra_material_is_found(self) -> None:
        cases = [
            ("Make the wall 3 200 mm long", ["3"]),
            ("Make the wall 3 m 20 cm high", ["20", "cm"]),
            ("Make the wall 3.2 m high and 20 cm", ["20", "cm"]),
            ("Make the wall - 2.5 m long", ["-"]),
            ("Make the wall + 2.5 m long", ["+"]),
            ("Make the wall \u22122.5 m long", ["\u2212"]),
            ("Make the wall \u2013 2.5 m long", ["\u2013"]),
            ("Make the wall \u20142.5 m long", ["\u2014"]),
            ("Make the wall 3 m and twenty centimetres high", ["twenty", "centimetres"]),
            ("Make the wall 3 m and a quarter high", ["quarter"]),
            ("Make the wall 3 m and \u00bd high", ["\u00bd"]),
            ("Make the wall 3 m and 20cm high", ["20cm"]),
            ("Make the wall 3 m and 20-cm high", ["cm"]),  # "20-cm" is identifier-shaped; the unit is not
            ("Make the wall 2 m and 1 dm long", ["1", "dm"]),
            ("Make the wall 2 m long, about 2 km", ["2", "km"]),
            ("Make the wall 3 m high, 10 ft", ["10", "ft"]),
            ("Make the wall 3 m high, ten feet", ["ten", "feet"]),
            ("Make the wall 3 m high or 120 inches", ["120", "inches"]),
            ("Make the wall 3 m high, 3 yards", ["3", "yards"]),
            ("Make the wall 3 m high in metres", ["metres"]),
            ("Make the wall 3 m high, 1e3", ["1e3"]),
            ("Make the wall 3 m high on level 2", ["2"]),  # conservative: unrelated numbers too
        ]
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)

    def test_complete_requests_have_none(self) -> None:
        for text in [
            "Make the selected wall 3200 mm high",
            "Make the selected wall 3.2 m high",
            "Make the selected wall 2500 mm long",
            "Make the selected wall 2.5 m long",
            "Set the wall thickness to 250 mm",
            "Set the wall thickness to 0.25 m",
            "Make the wall .5 m thick",
            "Make the wall 3.2m high",
            "Make the wall 3.20 metres high",
            "Make the wall 3.2 M high",
            "Make the wall 3.2\u00a0m high",
            "Thickness: 180 mm",
            "Height: 2.75 m.",
            "Make the wall 3.2 m-high",
            "Make wall d33-wall-001 3.2 m high",          # digits inside a GUID are not quantities
            "Make wall D33-WALL-001 3.2 m high",
            "Make wall 550e8400-e29b-41d4-a716-446655440000 3.2 m high",
            "Make wall 2O2Fr$t4X7Zf8NOew3FLOH 3.2 m high",  # IFC GlobalId
            "Set the IfcWall height to 3.2 m",
            "Make this one 3.2 m high, I'm sure",
            "Make the wall high",                          # no quantity at all: nothing extra
        ]:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), [])

    def test_signs_before_the_quantity_are_found(self) -> None:
        # Day-37b: any whitespace between sign and number, and every supported sign form.
        cases = [
            ("Make the wall - 2.5 m long", ["-"]),
            ("Make the wall -  2.5 m long", ["-"]),             # two spaces
            ("Make the wall -\t2.5 m long", ["-"]),             # tab
            ("Make the wall -\u00a0\u00a02.5 m long", ["-"]),   # two non-breaking spaces
            ("Make the wall + 2.5 m long", ["+"]),
            ("Make the wall minus 2.5 m long", ["minus"]),
            ("Make the wall Negative  2.5 m long", ["Negative"]),
        ]
        for sign in ("\u2010", "\u2011", "\u2212", "\u2013", "\u2014", "\ufe63", "\uff0d"):
            cases += [(f"Make the wall {sign}2.5 m long", [sign]),     # touching
                      (f"Make the wall {sign} 2.5 m long", [sign])]    # spaced
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)

    def test_qualified_or_range_quantities_are_found(self) -> None:
        cases = [
            ("Make the wall ~3.2 m high", ["~"]),
            ("Make the wall ~ 3.2 m high", ["~"]),
            ("Make the wall \u22483.2 m high", ["\u2248"]),
            ("Make the wall <3.2 m high", ["<"]),
            ("Make the wall > 3.2 m high", [">"]),
            ("Make the wall <=3.2 m high", ["<="]),
            ("Make the wall \u22643.2 m high", ["\u2264"]),
            ("Make the wall \u22653.2 m high", ["\u2265"]),
            ("Make the wall \u00b13.2 m high", ["\u00b1"]),
            ("Make the wall 3.2 m+ high", ["+"]),
            ("Make the wall 3.2 m \u00b1 high", ["\u00b1"]),
            ("Make the wall approximately 3.2 m high", ["approximately"]),
            ("Make the wall approx. 3.2 m high", ["approx."]),
            ("Make the wall Approx 3.2 m high", ["Approx"]),
            ("Make the wall 3.2 m approx. high", ["approx."]),
            ("Make the wall about 3.2 m high", ["about"]),
            ("Make the wall at least 3.2 m high", ["at least"]),
            ("Make the wall at most 3.2 m high", ["at most"]),
            ("Make the wall more than 3.2 m high", ["more than"]),
            ("Make the wall less than 3.2 m high", ["less than"]),
            ("Make the wall 2.5\u20133 m high", ["\u2013", "2", "5"]),   # range
            ("Make the wall 2.5 to 3 m high", ["2", "5"]),
            ("Make the wall between 2.5 and 3 m high", ["2", "5"]),
        ]
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)

    def test_punctuation_not_attached_to_the_quantity_is_not_found(self) -> None:
        for text in [
            "Make the selected wall 3.2 m high - thanks",
            "North wall \u2013 make it 3.2 m high",
            "Make the selected wall 3.2 m high (please)",
            "Make the selected wall 3.2 m high ~ thanks",
            "Height = 3.2 m",
            "Height: 3.2 m.",
            "Tell me about it: make the selected wall 3.2 m high",
            "Make the selected wall 3.2 m high; about time",
            "Make the selected wall 3.2 m high, at least today",
            "Minus the door trim, make the selected wall 3.2 m high",
        ]:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), [])

    def test_identifier_cases_document_the_behaviour(self) -> None:
        # Day-37b review cases. Only "x2" changed (a multiplier, now found); the
        # others are deliberate limitations: exempting them would also exempt
        # hyphen-joined or digit-led quantity material ("20-cm", "20cm", "1e3").
        cases = [
            ("Make wall wall-m-01 3.2 m high", ["m"]),        # unit word inside an id: rejected
            ("Make wall north-ten-01 3.2 m high", ["ten"]),   # number word inside an id: rejected
            ("Make the wall 3.2 m high x2", ["x2"]),
            ("Make the wall 3.2 m high X3", ["X3"]),
            ("Make the wall 3.2 m high v2", []),              # starts with a letter: identifier
            ("Make wall 1234 3.2 m high", ["1234"]),          # all digits: a number
            ("Make wall 3f2a9c 3.2 m high", ["3f2a9c"]),      # digit-led: like "20cm"
            ("Make wall f2a9c3 3.2 m high", []),              # letter-led hex: identifier
            ("Make wall d33-wall-001 3.2 m high", []),
            ("Make wall 550e8400-e29b-41d4-a716-446655440000 3.2 m high", []),
        ]
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)

    def test_hyphen_joined_wording_with_a_number_is_found(self) -> None:
        # Day-37c (Day-38b X26/X27): a number hidden in hyphen-joined wording.
        cases = [
            ("Make the wall 3 m high-and-20", ["high-and-20"]),
            ("Make the wall 3 m high-and-20cm", ["high-and-20cm"]),
            ("Make the wall 3 m long-plus-200", ["long-plus-200"]),
            ("Make the wall 3 m high-or-30", ["high-or-30"]),
            ("Make wall wall-high-01 3.2 m high", ["wall-high-01"]),  # limitation: such an id is rejected
        ]
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)
        for text in (
            "Make wall d33-wall-001 3.2 m high",
            "Make the selected wall (Wall-07) 2.5 m long",
            "Make wall 550e8400-e29b-41d4-a716-446655440000 3.2 m high",
            "Make wall 2O2Fr$t4X7Zf8NOew3FLOH 3.2 m high",
            "Make the load-bearing wall 3.2 m high",
            "Make the 2-storey wall 3.2 m high",
        ):
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), [])

    def test_day37c_qualifiers_are_found(self) -> None:
        cases = [
            ("Make the wall roughly 3.2 m high", ["roughly"]),
            ("Make the wall Roughly, 3.2 m high", ["Roughly"]),
            ("Make the wall around 3 m high", ["around"]),
            ("Make it up to 3.2 m high", ["up to"]),
            ("Make the wall under 3 m high", ["under"]),
            ("Make the wall over 3 m high", ["over"]),
            ("Make the wall (approx.) 3.2 m high", ["approx."]),
            ("Make the wall around about 3 m high", ["about"]),       # the adjacent one
            ("Make it 3.2 m high or so", ["high or so"]),
            ("Make the wall 3.2 m or so high", ["or so"]),
            ("Make the wall 3.2 m high, roughly", ["high, roughly"]),
            ("Make the wall 3.2 m high, approximately", ["high, approximately"]),
            ("Make the wall 3.2 m high (approx.)", ["high (approx."]),
            ("Make the wall 3 m high or under", ["high or under"]),
            ("Make the wall 3 m long or over", ["long or over"]),
            ("Make the wall 3 m around", ["around"]),
        ]
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), expected)

    def test_unrelated_uses_of_qualifier_words_are_not_found(self) -> None:
        for text in [
            "Up to you: make the selected wall 3.2 m high",
            "Over the weekend, make the selected wall 3.2 m high",
            "Walk around, then make the selected wall 3.2 m high",
            "Make the selected wall 3.2 m high under the current plan",
            "Make the selected wall 3.2 m high or leave it",
            # Limitation: a qualifier separated from the quantity by other words
            # is not associated with it.
            "Make the wall 3.2 m high, it is roughly right",
            "Make the wall 3.2 m high, give or take",
        ]:
            with self.subTest(text):
                self.assertEqual(provenance.unaccounted_quantity_material(text), [])
        # Conservative: a qualifier after the property word is found even across a sentence end.
        self.assertEqual(provenance.unaccounted_quantity_material("Make the wall 3.2 m high. Roughly done."),
                         ["high. Roughly"])

    def test_ascii_arrow_is_not_a_comparison(self) -> None:
        for text, expected in [
            ("Height -> 3.2 m", []),
            ("Height --> 3.2 m", []),
            ("Height > 3.2 m", [">"]),
            ("Height >= 3.2 m", [">="]),
            ("Height => 3.2 m", [">"]),     # may mean >=: still a comparison
            ("Height - > 3.2 m", [">"]),    # not an arrow
            ("Height -> -3.2 m", None),      # touching sign: no quantity at all
            ("Height -> - 3.2 m", ["-"]),
        ]:
            with self.subTest(text):
                if expected is None:
                    self.assertEqual(provenance.extract_quantities(text), [])
                else:
                    self.assertEqual(provenance.unaccounted_quantity_material(text), expected)


class VerifyProvenanceTest(unittest.TestCase):
    """verify_provenance() on already-validated edits."""

    def assert_rejected(self, edit: ValidatedPropertyEdit, text: str, reason: RejectionReason) -> None:
        with self.assertRaises(CommandRejected) as caught:
            provenance.verify_provenance(edit, text)
        self.assertEqual(caught.exception.reason, reason)

    def test_accepts_same_canonical_value_in_any_supported_unit(self) -> None:
        for edit in (accepted_edit(3200, 3200, "mm"), accepted_edit(3200, 3.2, "m"), accepted_edit(3200.0, 3200.0, "mm")):
            with self.subTest(edit=edit):
                q = provenance.verify_provenance(edit, "Make the wall 3.2 m high")
                self.assertEqual(q.value_mm, Decimal(3200))

    def test_rejections(self) -> None:
        self.assert_rejected(accepted_edit(3_200_000, 3200, "m"), "Make the wall 3.2 m high", R.QUANTITY_MISMATCH)
        self.assert_rejected(accepted_edit(3200, 3200, "mm"), "Make the wall longer", R.NO_EXPLICIT_QUANTITY)
        self.assert_rejected(accepted_edit(3000, 3, "m"), "Make it 3 m high and 4 m long", R.AMBIGUOUS_QUANTITY)
        self.assert_rejected(accepted_edit(2000, 2, "m"), "Make the wall 2 m longer", R.RELATIVE_CHANGE_UNSUPPORTED)

    def test_partial_quantity_is_rejected_even_when_the_model_repeats_it(self) -> None:
        # Day-37: each edit is what the parser alone would have matched.
        cases = [
            ("Make the wall 3 200 mm long", accepted_edit(200, 200, "mm", "Length")),
            ("Make the wall 3 m 20 cm high", accepted_edit(3000, 3, "m")),
            ("Make the wall 3.2 m high and 20 cm", accepted_edit(3200, 3.2, "m")),
            ("Make the wall - 2.5 m long", accepted_edit(2500, 2.5, "m", "Length")),
            ("Make the wall \u22122.5 m long", accepted_edit(2500, 2.5, "m", "Length")),
            ("Make the wall \u20132.5 m long", accepted_edit(2500, 2.5, "m", "Length")),
            ("Make the wall \u20142.5 m long", accepted_edit(2500, 2.5, "m", "Length")),
            ("Make the wall 3 m and twenty centimetres high", accepted_edit(3000, 3, "m")),
            ("Make the wall 3 m and a quarter high", accepted_edit(3000, 3, "m")),
        ]
        for text, edit in cases:
            with self.subTest(text):
                self.assertEqual(len(provenance.extract_quantities(text)), 1)
                with self.assertRaises(CommandRejected) as caught:
                    provenance.verify_provenance(edit, text)
                self.assertEqual(caught.exception.reason, R.AMBIGUOUS_QUANTITY)
                self.assertIn("besides", str(caught.exception))

    def test_two_explicit_quantities_keep_the_established_ambiguity_rejection(self) -> None:
        cases = [
            ("Make the wall 3.2 m high, not 3 m", accepted_edit(3200, 3.2, "m")),
            ("Make the wall 3.2 metres high and 200 mm thick", accepted_edit(3200, 3.2, "m")),
            ("Make the wall 3.2 m high, approximately 3 m", accepted_edit(3200, 3.2, "m")),
            ("Make it 3 m high and 4 m long", accepted_edit(3000, 3, "m")),
        ]
        for text, edit in cases:
            with self.subTest(text):
                with self.assertRaises(CommandRejected) as caught:
                    provenance.verify_provenance(edit, text)
                self.assertEqual(caught.exception.reason, R.AMBIGUOUS_QUANTITY)
                self.assertIn("states 2 dimensions", str(caught.exception))

    def test_module_is_pure(self) -> None:
        source = (BACKEND_ROOT / "services" / "commands" / "provenance.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertLessEqual(imported, {"__future__", "re", "dataclasses", "decimal", "services.commands"})
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(used & {"execute_command", "set_prop", "render", "executor", "EntityStore"})


class VerifyPropertyTest(unittest.TestCase):
    """verify_property(): the proposed property must be the one the user's wording names."""

    def test_accepted(self) -> None:
        cases = [
            ("Make the wall 3.2 m high", "Height"),
            ("Make the wall 3.2 m tall", "Height"),
            ("Set the height to 3.2 m", "Height"),
            ("Make the wall 4 m long", "Length"),
            ("Set the Length to 4 m", "Length"),
            ("Make the wall 250 mm thick", "Thickness"),
            ("Thickness: 250 mm", "Thickness"),
        ]
        for text, prop in cases:
            with self.subTest(text):
                self.assertEqual(provenance.verify_property(accepted_edit(1, 1, "mm", prop), text), prop)

    def test_rejected(self) -> None:
        cases = [
            ("Make the wall 3.2 m high", "Length", R.PROPERTY_MISMATCH),
            ("Make the wall 4 m long", "Height", R.PROPERTY_MISMATCH),
            ("Make the wall 250 mm thick", "Height", R.PROPERTY_MISMATCH),
            ("Make the wall 3.2 m", "Height", R.NO_EXPLICIT_PROPERTY),
            ("Make the wall 3.2 m wide", "Height", R.NO_EXPLICIT_PROPERTY),
            ("Make the long wall 3.2 m high", "Height", R.AMBIGUOUS_PROPERTY),
        ]
        for text, prop, reason in cases:
            with self.subTest(text):
                with self.assertRaises(CommandRejected) as caught:
                    provenance.verify_property(accepted_edit(1, 1, "mm", prop), text)
                self.assertEqual(caught.exception.reason, reason)


class VerifyTargetTest(unittest.TestCase):
    """verify_target(): a reference to the selected wall must match the selected GUID."""

    def test_selection_reference_must_match_the_selected_guid(self) -> None:
        for text in ("Make the selected wall 3.2 m high", "Make this wall 3.2 m high",
                     "Set the selection's height to 3.2 m"):
            with self.subTest(text):
                provenance.verify_target(accepted_edit(1, 1, "mm", guid=WALL), text, WALL)
                for selected in (OTHER_WALL, None):
                    with self.assertRaises(CommandRejected) as caught:
                        provenance.verify_target(accepted_edit(1, 1, "mm", guid=WALL), text, selected)
                    self.assertEqual(caught.exception.reason, R.TARGET_MISMATCH)

    def test_no_selection_reference_is_not_checked(self) -> None:
        # Scope of this check only: without a selection reference the target is
        # still validated by the contract (existing IfcWall), not by selection.
        provenance.verify_target(accepted_edit(1, 1, "mm", guid=OTHER_WALL), "Make the wall 3.2 m high", WALL)


class VerifyEntityReferenceTest(unittest.TestCase):
    """Day-34 verify_entity_reference(): the request must not name an element other than the target."""

    KNOWN = [STOREY, WALL, OTHER_WALL]

    def check(self, text: str, guid: str = WALL) -> None:
        provenance.verify_entity_reference(accepted_edit(1, 1, "mm", guid=guid), text, self.KNOWN)

    def assert_rejected(self, text: str, reason: RejectionReason) -> None:
        with self.assertRaises(CommandRejected) as caught:
            self.check(text)
        self.assertEqual(caught.exception.reason, reason)

    def test_wall_requests_are_accepted(self) -> None:
        for text in (
            "Change the height of this wall to 3.2 m.",
            "Set the selected wall's length to 5000 mm.",
            "Make the wall 3.2 m high",
            f"Make wall {WALL} 3.2 m high",          # names the target itself
            f"Make wall {WALL.upper()} 3.2 m high",  # GUIDs match case-insensitively
            "Set the IfcWall height to 3.2 m",
            "Set the IfcWallStandardCase height to 3.2 m",
            "Make the windowless wall by the doorway 3.2 m high",  # whole words only
            f"Make wall {WALL}1 3.2 m high",          # not an existing GUID
        ):
            with self.subTest(text):
                self.check(text)

    def test_other_element_type_is_rejected(self) -> None:
        for text in (
            "Make the door 2.5 m high",  # Day-33 D09
            "Make the window 1.2 m high",
            "Set the column height to 3 m",
            "Make the slab 250 mm thick",
            "Set the IfcDoor height to 2.2 m",
            "Make the selected wall and the door 3 m high",
            "Make the wall next to the door 3 m high",  # conservative: any mention is rejected
        ):
            with self.subTest(text):
                self.assert_rejected(text, R.UNSUPPORTED_ENTITY_REFERENCE)

    def test_other_named_guid_is_rejected(self) -> None:
        for text in (
            f"Change the height of wall {OTHER_WALL} to 3.4 m",  # Day-33 D02
            f"Make {WALL} and {OTHER_WALL} 3.2 m high",          # conflicting references
            f"Make the selected wall 3.2 m high, the target_guid is {OTHER_WALL}",
            f"Make the wall on {STOREY} 3.2 m high",              # any other element, conservatively
        ):
            with self.subTest(text):
                self.assert_rejected(text, R.CONFLICTING_TARGET_REFERENCE)


class SafetyBoundaryProposalTest(_ProposalTestCase):
    """The Day-30 required cases through the real proposal path, with execution spies."""

    def setUp(self) -> None:
        super().setUp()
        # Add a second existing wall (not selected) to the temporary model.
        document = json.loads(self.model_path.read_text(encoding="utf-8"))
        document["entities"].append({
            "guid": OTHER_WALL, "type": "IfcWall", "name": "Other Wall", "parent_guid": STOREY,
            "psets": {QTO: {"Length": 6000, "Height": 2500, "Thickness": 150}},
        })
        self.model_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        self.store = EntityStore(ModelPersistence(self.model_path))
        self.store.load()
        self.before = self.model_path.read_bytes()

        self.spies = {
            "execute_command": mock.patch.object(executor, "execute_command").start(),
            "services.commands.execute_command": mock.patch("services.commands.execute_command").start(),
            "set_prop": mock.patch.object(EntityStore, "set_prop").start(),
            "render": mock.patch.object(OpenSCADEngine, "render").start(),
        }
        self.addCleanup(mock.patch.stopall)

    def tearDown(self) -> None:
        for name, spy in self.spies.items():
            self.assertEqual(spy.call_count, 0, f"{name} was called")
        self.assert_model_unchanged()
        super().tearDown()

    async def assert_accepted(self, prompt: str, reply: str, prop: str, value_mm) -> None:
        response = await self.ask(replying(reply), prompt=prompt)
        self.assertEqual(response.intent, Intent.EDIT_PROPERTY, response.text)
        (command,) = response.commands
        self.assertEqual(command.type, CommandType.EDIT_PROPERTY)
        self.assertEqual(command.params, edit_params(prop, value_mm, "mm"))
        self.assertIs(type(command.params["value"]), type(value_mm))
        self.assertIn(f"to {value_mm} mm", response.text)

    async def assert_rejected(self, prompt: str, reply: str, reason: RejectionReason) -> None:
        response = await self.ask(replying(reply), prompt=prompt)
        self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
        self.assertIn(f": {reason.value} (", response.text)
        self.assertIn("No change was proposed.", response.text)

    async def test_valid_requests_are_accepted(self) -> None:
        cases = [
            ("Make the wall 3.2 m high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the wall 3.2 m high", proposal("Height", 3200, "mm"), "Height", 3200),
            ("Make the wall 4 metres long", proposal("Length", 4, "m"), "Length", 4000),
            ("Make the wall 250 mm thick", proposal("Thickness", 250, "mm"), "Thickness", 250),
            ("Make the wall 0.05 m thick", proposal("Thickness", 0.05, "m"), "Thickness", 50),
            ("Make the wall 0.05 m thick", proposal("Thickness", 50, "mm"), "Thickness", 50),
        ]
        for prompt, reply, prop, value_mm in cases:
            with self.subTest(prompt=prompt, reply=reply):
                await self.assert_accepted(prompt, reply, prop, value_mm)

    async def test_wrong_conversion_or_unit_is_rejected(self) -> None:
        cases = [
            ("Make the wall 3.2 m high", proposal("Height", 3200, "m"), R.VALUE_OUT_OF_RANGE),  # Day-27 bound first
            ("Make the wall 0.05 m thick", proposal("Thickness", 50, "m"), R.QUANTITY_MISMATCH),
            ("Make the wall 250 mm thick", proposal("Thickness", 250, "m"), R.VALUE_OUT_OF_RANGE),  # 250 m > 100 m
            ("Make the wall 25 mm thick", proposal("Thickness", 25, "m"), R.QUANTITY_MISMATCH),
            ("Make the wall 0.05 m thick", proposal("Thickness", 0.05, "mm"), R.QUANTITY_MISMATCH),
            ("Make the wall 4 metres long", proposal("Length", 40, "m"), R.QUANTITY_MISMATCH),
            ("Make the wall 3.2 m high", proposal("Height", 3.3, "m"), R.QUANTITY_MISMATCH),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt, reply=reply):
                await self.assert_rejected(prompt, reply, reason)

    async def test_small_wrong_conversion_below_the_bound_is_caught_only_by_provenance(self) -> None:
        # Same error class as "3.2 m" -> 3200 m, but small enough to pass the
        # 100 m bound: only provenance can catch it.
        await self.assert_rejected("Make the wall 0.08 m thick", proposal("Thickness", 80, "m"), R.QUANTITY_MISMATCH)

    async def test_invented_values_are_rejected(self) -> None:
        cases = [
            ("Make the wall longer", proposal("Length", 1112, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall taller", proposal("Height", 3500, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall thicker", proposal("Thickness", 0.3, "m"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall three metres high", proposal("Height", 3, "m"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 30 cm thick", proposal("Thickness", 300, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 10 ft long", proposal("Length", 3048, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 3,2 m high", proposal("Height", 3.2, "m"), R.NO_EXPLICIT_QUANTITY),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, reason)

    async def test_relative_and_ambiguous_requests_are_rejected(self) -> None:
        cases = [
            ("Make the wall 2 m longer", proposal("Length", 2, "m"), R.RELATIVE_CHANGE_UNSUPPORTED),
            ("Increase the height by 500 mm", proposal("Height", 500, "mm"), R.RELATIVE_CHANGE_UNSUPPORTED),
            ("Make the wall 3 m high and 4 m long", proposal("Height", 3, "m"), R.AMBIGUOUS_QUANTITY),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, reason)

    async def test_partial_or_signed_quantity_is_rejected_even_when_the_model_repeats_it(self) -> None:
        # Day-37: every reply is the parser's partial interpretation of the prompt.
        cases = [
            ("Make the wall 3 200 mm long", proposal("Length", 200, "mm")),
            ("Make the wall 3 m 20 cm high", proposal("Height", 3, "m")),
            ("Make the wall 3.2 m high and 20 cm", proposal("Height", 3.2, "m")),
            ("Make the wall - 2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \u22122.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \u20132.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \u20142.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall 3 m and twenty centimetres high", proposal("Height", 3, "m")),
            ("Make the wall 3 m and a quarter high", proposal("Height", 3, "m")),
            ("Make the wall 3.2 m high, not 3 m", proposal("Height", 3.2, "m")),
            ("Make the wall 3.2 metres high and 200 mm thick", proposal("Height", 3.2, "m")),
            ("Make the wall 3.2 m high, approximately 3 m", proposal("Height", 3.2, "m")),
        ]
        for prompt, reply in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, R.AMBIGUOUS_QUANTITY)

    async def test_existing_safe_rejections_keep_their_reasons(self) -> None:
        cases = [
            ("Make the wall 3.2 high", proposal("Height", 3.2, "m"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 2.5 m", proposal("Length", 2.5, "m"), R.NO_EXPLICIT_PROPERTY),
            ("Make the wall high", proposal("Height", 3000, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 0 m high", proposal("Height", 0, "m"), R.VALUE_OUT_OF_RANGE),
            ("Make the wall 3200 m high", proposal("Height", 3200, "m"), R.VALUE_OUT_OF_RANGE),
            ("Make the wall 30 cm high", proposal("Height", 300, "mm"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall 1.5e3 mm high", proposal("Height", 1500, "mm"), R.NO_EXPLICIT_QUANTITY),
            (f"Change the height of wall {OTHER_WALL} to 3.4 m", proposal("Height", 3.4, "m"),
             R.CONFLICTING_TARGET_REFERENCE),
            ("Make the door 2.5 m high", proposal("Height", 2.5, "m"), R.UNSUPPORTED_ENTITY_REFERENCE),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, reason)

    async def test_complete_quantities_are_still_accepted(self) -> None:
        cases = [
            ("Make the selected wall 3200 mm high", proposal("Height", 3200, "mm"), "Height", 3200),
            ("Make the selected wall 3.2 m high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the selected wall 2500 mm long", proposal("Length", 2500, "mm"), "Length", 2500),
            ("Make the selected wall 2.5 m long", proposal("Length", 2.5, "m"), "Length", 2500),
            ("Set the wall thickness to 250 mm", proposal("Thickness", 250, "mm"), "Thickness", 250),
            ("Set the wall thickness to 0.25 m", proposal("Thickness", 0.25, "m"), "Thickness", 250),
            ("Make the wall .5 m thick", proposal("Thickness", 0.5, "m"), "Thickness", 500),
            ("Make the wall 3.2m high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the wall 3.20 metres high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the wall 3.2 M high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the wall 3.2\u00a0m high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Thickness: 180 mm", proposal("Thickness", 180, "mm"), "Thickness", 180),
            (f"Make wall {WALL} 3.2 m high", proposal("Height", 3.2, "m"), "Height", 3200),
            ("Make the wall 3.2 m-high", proposal("Height", 3.2, "m"), "Height", 3200),
        ]
        for prompt, reply, prop, value_mm in cases:
            with self.subTest(prompt=prompt):
                await self.assert_accepted(prompt, reply, prop, value_mm)

    async def test_day37b_signed_or_qualified_quantity_is_rejected_even_when_the_model_repeats_it(self) -> None:
        cases = [
            ("Make the wall -  2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall -\u00a0\u00a02.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall -\t2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \u20102.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \u20112.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \ufe632.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall \uff0d2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall minus 2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall negative 2.5 m long", proposal("Length", 2.5, "m")),
            ("Make the wall ~3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall <3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall \u22653.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall 3.2 m+ high", proposal("Height", 3.2, "m")),
            ("Make the wall \u00b13.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall at least 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall at most 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall approximately 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall approx. 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall about 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the wall 2.5\u20133 m high", proposal("Height", 3, "m")),
            ("Make the wall 3.2 m high x2", proposal("Height", 3.2, "m")),
            ("Make the wall 3 200 mm long", proposal("Length", 200, "mm")),
            ("Make the wall 3 m 20 cm high", proposal("Height", 3, "m")),
        ]
        for prompt, reply in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, R.AMBIGUOUS_QUANTITY)

    async def test_day37b_earlier_checks_keep_their_reasons(self) -> None:
        cases = [
            # A touching ASCII sign was never parsed as a quantity.
            ("Make the wall -2.5 m long", proposal("Length", 2.5, "m"), R.NO_EXPLICIT_QUANTITY),
            ("Make the wall +2.5 m long", proposal("Length", 2.5, "m"), R.NO_EXPLICIT_QUANTITY),
            # "more"/"less" are relative wording, checked before the guard.
            ("Make the wall more than 3.2 m high", proposal("Height", 3.2, "m"), R.RELATIVE_CHANGE_UNSUPPORTED),
            ("Make the wall less than 3.2 m high", proposal("Height", 3.2, "m"), R.RELATIVE_CHANGE_UNSUPPORTED),
            # Two explicit quantities keep the established ambiguity rejection.
            ("Make the wall 2.5 m to 3 m high", proposal("Height", 3, "m"), R.AMBIGUOUS_QUANTITY),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, reason)

    async def test_exact_quantities_with_unrelated_punctuation_are_accepted(self) -> None:
        cases = [
            "Make the selected wall 3.2 m high - thanks",
            "North wall \u2013 make it 3.2 m high",
            "Make the selected wall 3.2 m high (please)",
            "Make the selected wall 3.2 m high ~ thanks",
            "Height = 3.2 m",
            "Tell me about it: make the selected wall 3.2 m high",
            "Make the selected wall 3.2 m high; about time",
        ]
        for prompt in cases:
            with self.subTest(prompt=prompt):
                await self.assert_accepted(prompt, proposal("Height", 3.2, "m"), "Height", 3200)

    async def test_day37c_hidden_numbers_and_qualifiers_are_rejected_even_when_the_model_repeats_them(self) -> None:
        cases = [
            ("Make the selected wall 3 m high-and-20", proposal("Height", 3, "m")),
            ("Make the selected wall 3 m high-and-20cm", proposal("Height", 3, "m")),
            ("Make the selected wall roughly 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the selected wall around 3 m high", proposal("Height", 3, "m")),
            ("Make the selected wall up to 3.2 m high", proposal("Height", 3.2, "m")),
            ("Make the selected wall under 3 m high", proposal("Height", 3, "m")),
            ("Make the selected wall over 3 m high", proposal("Height", 3, "m")),
            ("Make the selected wall 3.2 m high or so", proposal("Height", 3.2, "m")),
            ("Make the selected wall 3.2 m high, approximately", proposal("Height", 3.2, "m")),
            ("Make the selected wall 3.2 m high, roughly", proposal("Height", 3.2, "m")),
            ("Height > 3.2 m", proposal("Height", 3.2, "m")),
        ]
        for prompt, reply in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, R.AMBIGUOUS_QUANTITY)

    async def test_day37c_valid_identifiers_arrows_and_unrelated_words_are_accepted(self) -> None:
        cases = [
            "Height -> 3.2 m",
            f"Make wall {WALL} 3.2 m high",
            "Make the selected wall (Wall-07) 3.2 m high",
            "Make the load-bearing wall 3.2 m high",
            "Up to you: make the selected wall 3.2 m high",
            "Over the weekend, make the selected wall 3.2 m high",
            "Make the selected wall 3.2 m high under the current plan",
        ]
        for prompt in cases:
            with self.subTest(prompt=prompt):
                await self.assert_accepted(prompt, proposal("Height", 3.2, "m"), "Height", 3200)

    def test_validate_ai_edit_applies_the_completeness_guard(self) -> None:
        def command(prop: str, value: object, unit: str) -> Command:
            return Command(type=CommandType.EDIT_PROPERTY, params=edit_params(prop, value, unit))

        edit = ai_edit.validate_ai_edit(command("Height", 3.2, "m"), self.store, f"Make wall {WALL} 3.2 m high", WALL)
        self.assertEqual(edit.value_mm, 3200)
        for prompt, cmd in (
            ("Make the wall 3 200 mm long", command("Length", 200, "mm")),
            ("Make the wall \u22122.5 m long", command("Length", 2.5, "m")),
            ("Make the wall 3 m and a quarter high", command("Height", 3, "m")),
        ):
            with self.subTest(prompt=prompt):
                with self.assertRaises(CommandRejected) as caught:
                    ai_edit.validate_ai_edit(cmd, self.store, prompt, WALL)
                self.assertEqual(caught.exception.reason, R.AMBIGUOUS_QUANTITY)

    async def test_day27_rules_still_apply_first(self) -> None:
        cases = [
            ("Make the wall 500 metres long", proposal("Length", 500, "m"), R.VALUE_OUT_OF_RANGE),
            ("Make the wall 3.2 m wide", proposal("Width", 3.2, "m"), R.UNSUPPORTED_PROPERTY),
            ("Create a door 900 mm wide",
             llm_reply({"type": "CreateComponent", "params": {"component_type": "door", "width": 900}}),
             R.UNSUPPORTED_COMMAND),
            ("Make the wall 3.2 m high",
             llm_reply({"type": "EditProperty", "params": {**edit_params(), "scad": "cube(1);"}}),
             R.MALFORMED_COMMAND),
        ]
        for prompt, reply, reason in cases:
            with self.subTest(prompt=prompt):
                await self.assert_rejected(prompt, reply, reason)

    async def test_property_must_match_the_users_wording(self) -> None:
        await self.assert_accepted("Make the wall 3.2 m high", proposal("Height", 3.2, "m"), "Height", 3200)
        await self.assert_rejected("Make the wall 3.2 m high", proposal("Length", 3.2, "m"), R.PROPERTY_MISMATCH)
        await self.assert_accepted("Make the wall 4 m long", proposal("Length", 4, "m"), "Length", 4000)
        await self.assert_rejected("Make the wall 4 m long", proposal("Height", 4, "m"), R.PROPERTY_MISMATCH)

    async def test_selected_wall_must_be_the_target(self) -> None:
        prompt = "Make the selected wall 3.2 m high"
        self.assertIsNotNone(self.store.get_entity(OTHER_WALL))  # the other target exists and is a wall
        response = await self.ask(replying(proposal("Height", 3.2, "m", WALL)), prompt=prompt, selected=WALL)
        self.assertEqual(response.commands[0].params, edit_params("Height", 3200, "mm", WALL))
        response = await self.ask(replying(proposal("Height", 3.2, "m", OTHER_WALL)), prompt=prompt, selected=WALL)
        self.assertEqual((response.intent, response.commands), (Intent.QUERY, []))
        self.assertIn(f": {R.TARGET_MISMATCH.value} (", response.text)
        self.assertEqual(self.store.get_entity(OTHER_WALL).psets[QTO], {"Length": 6000, "Height": 2500, "Thickness": 150})

    async def test_no_number_and_no_command_proposes_nothing(self) -> None:
        response = await self.ask(replying(llm_reply(intent="query", text="Please give a dimension.")),
                                  prompt="Make the wall longer")
        self.assertEqual(response.commands, [])


if __name__ == "__main__":
    unittest.main()

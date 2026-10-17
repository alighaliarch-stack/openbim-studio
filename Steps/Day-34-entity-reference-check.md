# Day 34 — Entity-Reference Check (target identity)

**Status:** COMPLETE — committed `fc1fa7b` "Day 34: Reject conflicting AI entity references" (2026-10-13), pushed to `origin/main` on 2026-10-13.

*Written retrospectively on 2026-10-17 (Day 51), from the Day 34 report and final review (session record of 2026-10-13) and the re-evaluation results in that session's scratchpad (`day34/`), which is outside the repository. Results are **historical** unless marked otherwise.*

## Objective

Close the validation gap found on Day 33 (D02, D09). A request that names a different wall, or a non-wall element, must not be accepted as an edit of the selected wall.

## Root cause

Every target check compared only the *model's* `target_guid` with the selection: `verify_selected_target` requires target = selection, and `verify_target` reacts only to "selected / selection / this wall". Nothing read the entity references in the **user's own text**, so a model that silently put the selected wall in place of the element the user named passed every check.

Reproduced against the old code with no language model:
- D02, with gemma's reply: accepted;
- D09, with a correctly converted value: accepted;
- also accepted: "Make d33-wall-001 and d33-wall-002 3.2 m high", "Set the IfcDoor height…", "Make the window 1.2 m high".

## Change

One new pure function, `provenance.verify_entity_reference()`. It is called inside the existing gate `validate_ai_edit` after `verify_selected_target`, so it runs at both proposal and confirmation.

1. **Another element type is named.** Whole words door, window, column, slab, beam, roof, stair(s), staircase, ceiling, railing (and plurals), or any IFC class name other than `IfcWall` / `IfcWallStandardCase`. Result: `unsupported_entity_reference`.
2. **Another existing GUID is named**, as a whole token, case-insensitive, other than the target. Result: `conflicting_target_reference`.

Two new `RejectionReason` values were added. `provenance.py` keeps its import whitelist: the existing GUIDs are passed in as a list. Nothing was loosened.

## Tests

- **`test_safety_boundary.py`, `VerifyEntityReferenceTest` (3 tests):** accepted wall wordings (including "windowless" and "doorway", whole-word matching); rejected type references; rejected GUID references.
- **`test_ai_property_bridge.py` (4 path tests):** D02 (and both GUIDs named); D09 with replies `2.5 m` and `2500 mm`; three legitimate selected-wall edits run through to confirm; a model claiming a different target (`target_mismatch`).
- Every rejection test asserts that `execute_command`, `set_prop` and `OpenSCADEngine.render` were not called and that the model file is byte-identical.

| Run (historical) | Result |
|---|---|
| Full backend suite | **93 tests, OK** (86 + 7 new; no existing test changed) |
| Mutation check (new check disabled in memory) | 14 sub-failures |

**Re-verified on 2026-10-17 (Day 50B):** the backend suite at HEAD `8561338` ran 114 tests, OK. It includes these tests.

## Re-evaluation on the unchanged Day 33 dataset (historical)

Same untracked runner and dataset (see `Steps/Day-33-live-prompt-evaluation.md`), same models and settings; no confirm call.

| Metric | llama3.2:3b Day 33 → Day 34 (×2) | gemma3:4b Day 33 → Day 34 |
|---|---|---|
| Correct (/41) | 38 · 38 → 38 · 38 | 40 → **41** |
| Unsafe acceptances | 0 → 0 | **1 (D02) → 0** |
| False rejections | 3 → 3 (A01, A03, D03, unit error) | 0 → 0 |

- In all 123 case-runs the model's raw command was identical to Day 33. The only outcome that changed was gemma's D02, now `conflicting_target_reference`.
- Under llama, D09 is still caught first by the range bound, because of the unit error. The bridge tests show the new check covers D09 independently of that error.

## Limitations

- **Entity names are not matched:** "Make the South Wall 3 m high" with the North Wall selected is not caught.
- **Identifiers that don't exist in the model** are not flagged.
- **English-only word list.**
- **Conservative rejections** of legitimate wall wording: "the stair core wall", "shear wall at column line A", "column-free wall", "slab-edge wall", "up to the ceiling", "match the door head", "IfcWalls", a storey GUID.
- **Non-wall words outside the list pass this check:** "opening", "pillar", "stairway", "fence", "lintel", "pipe", "curtain wall". They still need the model to target the selected wall, and an explicit Apply.
- `services/commands/__init__.py` does not re-export `verify_entity_reference` (nothing imports it from the package).
- 41-prompt evaluation at temperature 0: preliminary evidence, not a reliability claim.

## Files changed (Day 34, `fc1fa7b`)

- `backend/services/commands/provenance.py` (`_OTHER_ELEMENT_TYPE`, `verify_entity_reference()`)
- `backend/services/commands/contract.py` (`UNSUPPORTED_ENTITY_REFERENCE`, `CONFLICTING_TARGET_REFERENCE`)
- `backend/services/commands/ai_edit.py` (one call in `validate_ai_edit`)
- `backend/tests/test_safety_boundary.py`
- `backend/tests/test_ai_property_bridge.py`

Not included: `backend/evaluation/` (untracked) and the scratchpad results.

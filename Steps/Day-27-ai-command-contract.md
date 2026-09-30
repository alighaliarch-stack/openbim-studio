# Day 27 — AI Command Contract

**Status:** COMPLETE

## Objective

Create the validated, typed command layer that sits between future natural-language/LLM interpretation and the existing deterministic BIM property/geometry pipeline. The first supported capability is deliberately narrow: edit an existing IfcWall's `Qto_WallBaseQuantities.Length`, `Height` or `Thickness`.

No LLM is integrated. Natural-language AI execution is not complete.

## Research Architecture

The AI path is split into four layers:

1. Natural-language interpretation (LLM) — **not implemented** (Day 28).
2. Structured command representation — **implemented** (`services/commands/contract.py`).
3. Command validation — **implemented** (`services/commands/contract.py`).
4. Command execution — **minimal bridge implemented** (`services/commands/executor.py`).

Validation is independent of any LLM and is tested directly.

## Existing AI Scaffold (inspection findings)

- Frontend `AIChatPanel` sends `POST /api/ai/prompt` with `{prompt, selected_guid}`; the backend route calls `AIChatService` (ContextBuilder → LLMClient → OutputParser).
- `LLMClient.complete` makes no provider call: without a key it raises `LLMClientError` (the "No API key configured" message); with a key it raises `NotImplementedError`, which the service does not catch.
- Nothing loads `backend/.env` (no `load_dotenv` call), although the error message tells users to put the key there.
- The LLM reply is validated against `AIResponse` (intent and command type are fixed lists), but `Command.params` is `dict[str, Any]` with no validation.
- The backend executes no commands. The frontend runs only `CreateComponent`, by calling `POST /api/scad/render` and refetching; `EditProperty` and `UpdateSelection` are silently ignored.
- `EntityStore.add_entity` has no callers, so no path creates an entity.

## Command Contract

Reuses the existing M09 envelope (`services/llm/models.py` `Command`) instead of a second command model. The only accepted command:

```json
{
  "type": "EditProperty",
  "params": {
    "target_guid": "<guid of an existing IfcWall>",
    "pset": "Qto_WallBaseQuantities",
    "property": "Length | Height | Thickness",
    "value": 3,
    "unit": "m"
  }
}
```

- `params` must contain exactly these five keys (unknown keys rejected); the envelope must contain only `type` and `params`.
- `value` must be a JSON number (not a string, boolean or null) and finite.
- `unit` is required and must be exactly `mm` or `m`.
- The normalised value must be > 0 mm and ≤ 100 000 mm (100 m).
- The target must exist, be an `IfcWall`, and already have the property (edit only, never create).

Accepted commands become a `ValidatedPropertyEdit` (target, pset, property, `value_mm`, and the original value and unit).

## Unit Normalisation

- `mm` × 1, `m` × 1000, computed with `Decimal` to avoid binary-float artefacts (2.8 × 1000 would otherwise be 2800.0000000000005).
- Whole results are stored as integers, matching existing model values: 3 m → 3000, 3.5 m → 3500, 2.8 m → 2800, 3000 mm → 3000; 212.5 mm stays 212.5.
- Every other unit (`cm`, `ft`, `M`, empty, or text) is rejected, never guessed.

## Rejections

Each rejection raises `CommandRejected` with a reason code and a readable detail, and changes nothing. Reason codes: `malformed_command`, `unsupported_command`, `unknown_entity`, `unsupported_target_type`, `unsupported_property`, `property_not_present`, `invalid_value`, `unsupported_unit`, `value_out_of_range`.

Rejected by the tests: Width; `Pset_WallCommon` and other arbitrary properties; nonexistent GUID; IfcDoor, IfcWindow and IfcBuildingStorey targets; zero; negative; above 100 m; NaN; ±Infinity; numeric strings; `"3m"`; boolean; null; `ft`, `cm`, `M`, empty unit; missing unit; `CreateComponent`, `CreateWall`, `MoveComponent`, `RotateComponent`, `DeleteComponent`, `UpdateSelection`; a direct SCAD render command; SCAD source as an extra param or extra command field; SCAD text as value, property or unit; non-object params or command. No request is converted into another command.

## Target Validation

An explicit `target_guid` is required and must name an existing IfcWall. No GUID is invented. The existing `AIPromptRequest.selected_guid` (the frontend's primary selection) already reaches the backend and could later supply the target, but Day 27 does not use it.

## Execution Boundary

`execute_command(command, store)` always validates first, then makes exactly one call: `EntityStore.set_prop(guid, pset, property, value_mm)` — the same call `POST /api/properties/{guid}` makes. Geometry is regenerated only by the existing chain:

EntityStore.set_prop → ParametricGeometry → GeometryFactory → WallGenerator → OpenSCADEngine → OpenSCAD → STL → GeometryRef

- The command package imports nothing from `services.openscad`, `services.geometry` or `api`, and makes no HTTP or subprocess call.
- It returns the updated entity and `geometry_regenerated` (false when the property saved but the GeometryRef URL did not change — the same signal as the Day 20 `geometry_warning`). Existing save semantics are unchanged.
- No HTTP route and no frontend change: the bridge is a backend service only.

## Tests

`backend/tests/test_command_contract.py` (Python `unittest`, no new dependencies, temporary model; execution tests use the real OpenSCAD CLI and skip if it is missing):

- Valid 1–5 plus extra normalisation cases (3.5 m, 0.25 m, 212.5 mm), including the existing `Command` model as input.
- 38 rejection cases, all with the expected reason; model unchanged on disk and in memory.
- Rejected commands never reach `EntityStore.set_prop` (mocked, asserted not called).
- NaN arriving through the existing `OutputParser` (Python's `json` accepts `NaN`) is rejected.
- Accepted commands update the property, produce a new STL with matching bounds, and persist to the temporary model.
- A call-stack trace at the moment OpenSCAD is invoked proves the order `execute_command → set_prop → ParametricGeometry.regenerate → GeometryFactory.regenerate → WallGenerator.generate`, exactly one render per command, and no API route in the path.
- A forced regeneration failure is reported (`geometry_regenerated` false) with the existing semantics.
- A static check that the command package imports no geometry, OpenSCAD, API, HTTP or subprocess module.

Mutation check (on a throwaway copy): removing the finite check, accepting `cm`, skipping validation in the executor, importing OpenSCAD in the executor, and adding a direct WallGenerator call were each caught by the intended tests.

## Validation Results

- Day 27 tests: 11 tests OK.
- Full backend suite (`python -m unittest discover -s tests -v`): 13 tests OK (includes the Day 25 workflow and config tests).
- `git diff --check`: clean.
- No frontend files changed (TypeScript check not required).
- No API key, `.env`, provider configuration, LLM prompt or `/api/scad/render` call added; `core/config.py`, `services/llm/` and `frontend/` unchanged.
- `backend/data/model.json`: untouched by the implementation and tests.
- Day 25 browser workflow (`node e2e/workflow.mjs`, run after the live dev backend on :8000 was stopped): 74/74 checks passed; the protected model.json hash was unchanged.

## Architectural Decisions

- Reuse the existing `Command` envelope and `CommandType.EDIT_PROPERTY`; type only its params.
- Whitelist by IFC type → property set → property (`EDITABLE_PROPERTIES`), limited to what WallGenerator turns into geometry.
- Require an explicit unit; only `mm` and `m`.
- MVP safety bound (confirmed Day 27 decision): accepted values must satisfy 0 < value ≤ 100 m. This is an application-level safety constraint for the MVP, not an IFC, BIM, structural, or physical maximum.
- Edit existing properties only (`property_not_present`), because `set_prop` would otherwise create new property sets.
- Execution always re-validates; there is no path to `set_prop` that skips validation.

## Deferred

- **Day 28:** natural-language interpretation — provider call, model configuration, `.env` loading, prompt describing this contract, handling of `NotImplementedError` and unknown `selected_guid` (currently HTTP 500).
- **Day 29:** safety boundary — the frontend's AI `CreateComponent` → `POST /api/scad/render` path bypasses EntityStore and creates no entity; `/api/scad/render` accepts unvalidated parameters (a string parameter was shown to inject SCAD code) and returns absolute file paths.
- **Day 30:** AI → property bridge — wiring `AIChatService` / an API route to `execute_command`, selection-based targets, and frontend display/confirmation.

## Files Changed

- `backend/services/commands/__init__.py`
- `backend/services/commands/contract.py`
- `backend/services/commands/executor.py`
- `backend/tests/test_command_contract.py`
- `Steps/Day-27-ai-command-contract.md`

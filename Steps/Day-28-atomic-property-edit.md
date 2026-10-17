# Day 28 — Atomic Property Edit

**Status:** IMPLEMENTED — awaiting review (not committed)

## Objective

Prevent a failed wall-geometry regeneration from leaving semantic properties and geometry inconsistent. Every rejected edit must leave both the in-memory model and the persisted `model.json` unchanged; a successful wall dimension edit must commit matching semantic properties and regenerated geometry.

This milestone came out of the research traceability audit. Before it, edits were saved even when regeneration failed (the Day 20/24 "save-then-warn" semantics). An exception during regeneration or persistence could also leave memory and disk out of step.

## Roadmap Adjustment

Atomic property editing is inserted as Day 28. The AI/parametric bridge sequence previously planned for Days 28–33 moves back by one day:

| Day | Milestone |
|---|---|
| 28 | Atomic property edit (this record) |
| 29 | Intent parsing / LLM interpretation (was Day 28) |
| 30 | Safety boundary, incl. the direct `/api/scad/render` route and the AI panel's `CreateComponent` path (was Day 29) |
| 31 | AI → property bridge (was Day 30) |
| 32 | AI regeneration test (was Day 31) |
| 33 | Prompt examples (was Day 32) |
| 34 | AI bridge checkpoint (was Day 33) |

Later phases shift accordingly. LLM integration and direct-SCAD-route hardening were deliberately not combined with this milestone.

## Before

`POST /api/properties/{guid}` → `EntityStore.set_prop()`:

1. mutated the live entity (`psets.setdefault(pset, {})[prop] = value`, which could also create a property set);
2. regenerated geometry on it (WallGenerator never raises; on failure it returns the previous `GeometryRef`);
3. persisted the whole model;
4. notified subscribers.

The route then set `geometry_warning` if the wall's geometry URL had not changed. Consequences:

- Invalid values (0, negative, strings, `null`, oversized) were saved with the old geometry.
- NaN/Infinity (accepted by Python's JSON parser) reached OpenSCAD.
- Exceptions during regeneration (engine missing, `.scad` write error, subprocess `OSError`) or persistence left memory changed but disk unchanged; the frontend's recovery refetch could then return an unsaved value.

## Transaction Semantics (approved)

| Outcome | HTTP | `code` | Memory | Disk | Notify |
|---|---|---|---|---|---|
| Success | 200 | — | new | new | yes |
| Invalid wall dimension | 422 | `validation_failed` | unchanged | unchanged | no |
| Wall dimension edit on a wall with no geometry | 422 | `no_existing_geometry` | unchanged | unchanged | no |
| Geometry regeneration failed (any cause) | 502 | `geometry_regeneration_failed` | unchanged | unchanged | no |
| Persistence failed | 500 | `persistence_failed` | unchanged | unchanged | no |
| Unknown GUID | 404 | — | unchanged | unchanged | no |

Errors use the existing `ErrorResponse` envelope (SDS 9.3) inside `detail`, with a new optional `code` field.

## Implementation

**Copy-on-write in `EntityStore.set_prop()`** — the live entity is never mutated:

1. If the property drives geometry: validate the value (finite JSON number, not boolean, 0 < value ≤ 100 000 mm) and require existing geometry.
2. Apply the edit to a deep copy of the entity.
3. If the property drives geometry: check that all the wall's stored inputs are present and valid (so a bad stored value is reported as such, not as a kernel failure). Then run `ParametricGeometry.regenerate()` on the copy. An exception, or an unchanged/missing `GeometryRef` URL, is a regeneration failure.
4. Persist the candidate model (`_persist(entities)`) through the existing atomic `ModelPersistence.save()`.
5. Only then replace the live model and notify subscribers. Subscriber errors are logged, so a committed edit is never reported as failed.

No rollback code or snapshot/restore is needed: until step 5 nothing live has changed, and `ModelPersistence.save()` never replaces `model.json` with a partial write.

**Properties that don't drive geometry** (e.g. `Pset_WallCommon.IsExternal`, `Qto_WallBaseQuantities.NetVolume`, door quantities) are saved without regenerating geometry. Their values are not type-checked (existing SDS TODO).

**Shared rules:** `services/ifc/geometry_inputs.py` defines the geometry-driving properties (`IfcWall` → `Qto_WallBaseQuantities.Length/Height/Thickness`), the Day 27 100 m bound and the dimension check. The Day 27 command contract imports the bound from it, and a test asserts that the contract's editable properties equal the geometry inputs. It lives in the semantic layer rather than `services/geometry/` because the Day 27 import-boundary test forbids `services.commands` from importing `services.geometry`.

**Unchanged contracts:** GeometryFactory, WallGenerator, ParametricGeometry, OpenSCADEngine, CLIExecutor, StlParser, TemplateRenderer, `ModelPersistence`, `PropertyEditResponse` (`geometry_warning` retained, now always `null`), ModelContext, SceneManager, MeshLoader.

**Changed contract:** `execute_command()` now raises `PropertyEditRejected` when regeneration or persistence fails, instead of returning a saved edit with `geometry_regenerated: false`. `geometry_regenerated` is retained and is always `true` on return.

**Frontend:** the existing failure path already reverts the optimistic value, shows the backend's text for 4xx and refetches; with atomic edits that refetch now always returns the committed state. The only change: a 5xx with a structured `ErrorResponse` shows the backend's message (e.g. "Geometry could not be regenerated; the change was not saved.") instead of the generic text, without the health probe. The 100 m bound is not mirrored in the UI (optional per the decision; the backend stays authoritative, and this lets the browser test exercise the real 422).

## Files Changed

- `backend/services/ifc/geometry_inputs.py` (new)
- `backend/services/ifc/entity_store.py`
- `backend/api/properties.py`
- `backend/api/schemas.py`
- `backend/services/commands/contract.py`
- `backend/services/commands/executor.py` (comments/docstring only)
- `backend/tests/test_property_transaction.py` (new)
- `backend/tests/test_command_contract.py`
- `frontend/src/hooks/useModel.ts`
- `frontend/e2e/workflow.mjs`
- `Steps/Day-28-atomic-property-edit.md`

## Tests

`backend/tests/test_property_transaction.py` — through the real route on a temporary model. After every request it asserts memory == `model.json` == `GET /api/model`; after every rejection the file is byte-identical and the in-memory model unchanged.

- `AtomicPropertyEditTest` (15 tests; mocks at real boundaries; needs no OpenSCAD, never skipped):
  - success commits property and matching bounds/URL;
  - invalid values for each wall dimension (0, -1, above 100 m, 10³⁰, strings, booleans, null, arrays, objects) → 422, renderer never called;
  - raw `NaN` / `Infinity` / `-Infinity` → 422;
  - 100 000 mm accepted (inclusive bound);
  - wall without geometry → 422 `no_existing_geometry`;
  - a rejected edit creates no property set;
  - invalid stored quantity → 422, not rendered;
  - regeneration failures → 502: missing binary, FAILED render, CLI timeout, non-zero exit, STL parse error, `RuntimeError`, subprocess `PermissionError`, no engine registered;
  - persistence failure → 500; a failed `os.replace` leaves no temporary file;
  - recovery after each failure class;
  - non-geometry properties skip regeneration;
  - subscribers notified only on commit, and a failing subscriber doesn't fail the edit;
  - unknown GUID → 404;
  - contract and store share the geometry-input rules.
- `RealOpenSCADEditTest` (2 tests; real CLI, skipped without it): 1 mm, 100 000 mm, 212.5 mm and 2800 mm accepted with STL bounds checked by an independent reader; recovery with a real render after a forced failure.

`backend/tests/test_command_contract.py`: `test_regeneration_failure_is_reported_not_hidden` replaced by `test_regeneration_failure_rejects_and_changes_nothing` (raises `PropertyEditRejected`; entity and file unchanged).

`frontend/e2e/workflow.mjs`: two rejected edits between the two edit rounds:

- **N1, a real backend rejection.** Length 100001 is sent to the real backend, which returns 422 `validation_failed`.
- **N2, an injected browser response, not a real backend or OpenSCAD failure.** The browser intercepts the POST with CDP Fetch and answers it with a 502 body in the backend's `ErrorResponse` shape. The backend is never called, so N2 tests frontend handling of a 502 only. Its N2c check (temporary model unchanged) is trivially true because the request never reaches the backend; it is not evidence of backend atomicity. Real 502 behaviour (OpenSCAD failures, and the model left unchanged in memory and on disk) is covered by `backend/tests/test_property_transaction.py`, not by N2.

Each scenario checks the status and `code`, the error message shown, the input reverted, no new STL fetched, the committed STL mesh shown and still selected, the temporary model unchanged, and that only the expected rejection errors were logged. Round 2 then proves recovery. The K check excludes only these two deliberately rejected requests.

Mutation check (throwaway copy outside the repository): mutating the live entity, never committing to memory, committing to memory before persisting, dropping the unchanged-URL check, dropping value validation, dropping the stored-input check and regenerating non-geometry wall properties were each caught by the suite; the unmutated control passed.

## Validation Results

- Backend before changes: `Ran 13 tests … OK`.
- Backend after: `Ran 30 tests … OK` (no skips; OpenSCAD 2021.01).
- Browser workflow: **84/84 checks passed** (74 existing + 10 new) on **three consecutive runs**; re-run twice more (84/84 each) after the pre-commit review corrected the N1/N2 labels.
- `npx tsc --noEmit -p tsconfig.json`: passed.
- `git diff --check`: clean.
- `backend/data/model.json` SHA-256 `23de08c289397aa27f45a3607096c6c1240b2297b727a58da7eaf92ed0be345f` before and after all runs; not staged.

### Nature of These Results

The backend and browser counts, the repeated runs and the mutation check are **functional regression, failure-injection and test-quality checks**. They show that the implemented behaviour matches its specification on the cases tested, and that the tests detect the faults they were designed to detect.

They are **not research experiments**. They give no measured evidence of geometry fidelity, latency, statistical performance or any generalisable research finding. A separate quantitative evaluation plan, and a dataset of measurements, are still outstanding.

## Known Limitations / Deferred

- The 502 detail is generic ("OpenSCAD did not produce new geometry"): WallGenerator discards `RenderResult.error`, and its contract was kept.
- **Generated-file risk:** every render attempt leaves its `.scad` file in the STL directory. Some failed attempts also leave an `.stl` file: an STL that fails to parse, or a good render whose edit then fails to persist. These files are never cleaned up, and all of them are served from `/geometry/`. A cleanup/retention policy is deferred hardening work; it was not implemented here.
- Values of properties that don't drive geometry are still not type-checked.
- `EntityStore.add_entity()` keeps the old mutate-then-persist order (no callers).
- `set_prop` is synchronous and runs inside an `async` route, so edits are serialised by the event loop; a lock would be needed if routes move to a thread pool.
- The browser 502 scenario (N2) is an injected response; no browser test observes a real OpenSCAD failure end to end.
- `ARCHITECTURE.md` and `README.md` still describe the pre-Day-20 flow (audit finding; not changed here).
- The `/api/scad/render` bypass is unchanged (Day 30 safety boundary).

## Subsequent updates (2026-10-17)

*Added during the Day 51 documentation update. Everything above is left as originally recorded.*

- **Status.** The "awaiting review (not committed)" status above is out of date. Day 28 was committed on 2026-10-04 as:
  - `cf4d49c` "Day 28: Make property edits atomic" (code and tests);
  - `3f36c15` "Day 28: Document atomic property-edit checkpoint" (this document).
- **Published.** The local remote-tracking reflog records the push of `3f36c15` to `origin/main` on 2026-10-06. On 2026-10-17 both commits are ancestors of `origin/main` (`8561338`).
- **Deferred items seen again later.** The generic 502 detail (OpenSCAD's failure reason not logged) and the missing STL cleanup were observed again on Days 38, 42 and 49 and are still open; see `Steps/Day-42-geometry-validation.md` and `Steps/Day-49-50-mvp-audit-and-checkpoint.md`.

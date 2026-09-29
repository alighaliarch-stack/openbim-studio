# Day 26 — MVP Interaction Checkpoint

**Status:** COMPLETE

**Checkpoint:** `Day 26: Restore Model Tree after backend recovery` (fix and this record in one commit)

## Objective

Audit whether the MVP interaction layer — selection, property editing, geometry update, navigation, failure/recovery and repeatability — is coherent and stable enough to close the interaction phase before the controlled AI bridge (Days 27–33). Audit and validation checkpoint; no feature development.

## Confirmed Interaction Architecture

Unchanged, confirmed from source:

InputField (blur/Enter) → `useModel.editProperty` (optimistic local update) → `POST /api/properties/{guid}` → `EntityStore.set_prop` → `ParametricGeometry` → `GeometryFactory` → `WallGenerator` → `OpenSCADEngine` → OpenSCAD → STL → `GeometryRef` → persistence → response → `ModelContext.applyConfirmedEntity` → `Viewport` → `MeshLoader` → `SceneManager.loadMeshFromStl`.

- Selection is GUID-keyed in `SelectionContext`, shared by viewport picking and the Model Tree. `Viewport` reapplies highlights whenever entities or selection change; `SceneManager` keeps each GUID's highlight mode across the asynchronous STL swap (Day 21).
- Navigation uses OrbitControls: right-drag orbit, middle-drag pan, wheel zoom; left button reserved for selection; double-click selects and focuses the current mesh (Day 22).
- Save semantics unchanged: property changed in memory → geometry regeneration → persistence. A failed regeneration keeps the new property with the previous geometry (Day 20/24 known limitation).

## Day 25 Regression (before the fix, at `fbb3e9e`)

- Backend workflow test: `Ran 2 tests … OK`.
- Browser workflow test: 74/74 checks passed.
- `npx tsc --noEmit -p tsconfig.json`: passed.

## Supplementary Interaction Audit

A scratchpad-only audit script (not committed), with the same isolation as the Day 25 browser test: its own backend on a temporary model via `MODEL_JSON_PATH`, Vite on :5199, headless Chrome. It covered what the Day 25 workflow does not:

- Selection: click-select; empty-canvas deselect; Ctrl+click toggle on/off; Model Tree ↔ viewport GUID sync; highlight cleared when another entity (storey) is selected.
- Navigation: orbit (camera moves, target fixed, distance kept); pan (target moves, camera offset kept); zoom (distance 12806 → 9414 mm); left-drag inert; double-click after orbit/pan/zoom focuses the regenerated mesh centre.
- Editing: loading state while the request is held ("Saving and regenerating geometry…", per-field "…", all inputs disabled, optimistic value shown); success ✓; STL mesh replaced; exactly one wall mesh in the scene (old mesh disposed); highlight kept; persistence.
- Validation failure: empty, `0` and `-250` rejected locally with the correct message, value reverted, no request sent; a following valid edit succeeds.
- HTTP 500 (forced): "The server could not save this change.", value reverted on screen and on disk, geometry and selection kept.
- Real OpenSCAD failure (backend restarted with an invalid binary): ⚠ geometry warning on the field, property saved, previous geometry kept, selection kept; feedback hidden when the storey is selected and shown again on the wall; after OpenSCAD is restored the next edit regenerates with all current dimensions and clears the warning.
- Backend down (backend stopped): "Could not reach the backend…", value reverted, mesh and selection kept; after restart the next edit saves and regenerates.

**Result before the fix: 27/28 checks passed.**

## Discovered Defect — Model Tree Recovery

After a backend outage, the Model Tree kept showing `Failed to load model: GET /model failed with status 500` even after the backend recovered and edits succeeded; only a page reload restored it.

Cause: `ModelContext.refetch()` sets `error` on failure, and only a later successful refetch cleared it. A successful edit goes through `applyConfirmedEntity`, which did not clear `error`, so `ModelTree` kept rendering the error instead of the retained last-good tree. Viewport selection, editing and focus were unaffected.

## Fix

One line in `frontend/src/context/ModelContext.tsx`, in `applyConfirmedEntity`:

    setError(null);

A backend-confirmed entity demonstrates that backend communication has recovered. The retained tree state is still valid for this edit (property edits do not change tree names or structure), so no additional refetch is performed. The alternative (triggering a refetch after recovery) was deliberately not implemented.

## Final Validation (after the fix)

- Supplementary interaction audit: **28/28 checks passed**. The Model Tree is visible again after backend recovery plus a successful edit, without a reload; the outage error is still shown while the backend is down.
- Day 25 backend workflow test: `Ran 2 tests … OK`.
- Day 25 browser workflow test: **74/74 checks passed**.
- `npx tsc --noEmit -p tsconfig.json`: passed.
- `git diff --check`: clean.
- No unexpected console errors or exceptions; only failure-path errors caused by the forced scenarios, plus Chrome's automatic `/favicon.ico` request. Backend log: no error lines.

## model.json Protection

`backend/data/model.json` was never read or written by any test (all runs used temporary models via `MODEL_JSON_PATH`). SHA-256 unchanged throughout: `168b3bcf25875606a556ddcee88b1610ec1ccb2cc21547b837d0fad29eae448d`. It remains an intentional, uncommitted working-tree modification.

## Deferred / Non-Blocking Issues

Observed and deliberately not fixed:

- `GridHelper` not rotated for the Z-up scene (the grid is a vertical plane at y = 0).
- Viewport canvas taller than a 900 px window.
- Generated STL files are never cleaned up.
- Every entity change reloads every mesh; during a backend outage the reload fails and the viewport falls back to the box placeholder. No STL-load failure UI.
- Model Tree still replaces the tree with the error message during an outage (display improvement deferred since Day 24).
- Ctrl/Shift-click toggles in the viewport but only appends in the Model Tree.
- Meshes of entities removed from the model are not removed (no delete path in the MVP).
- `backend/.env.example` does not list `MODEL_JSON_PATH`.
- Property saved even when regeneration fails; geometry catches up on the next successful edit.
- Browser tests observe SceneManager via the React fiber (read-only); the Vite proxy port (8000) is fixed.
- OpenSCAD is called synchronously inside an async route.
- Server-side type/range validation; broader IFC schema coverage; multi-component geometry.

## Day 29 Safety-Boundary Concern

The existing AI chat panel's `CreateComponent` handling calls `POST /api/scad/render` directly and then refetches the model. That path bypasses the property-edit pipeline (EntityStore → ParametricGeometry → GeometryFactory → WallGenerator). It must be addressed by the Day 29 safety boundary so AI actions cannot bypass the deterministic geometry pipeline. Not changed on Day 26.

## AI

No AI functionality was implemented or modified. Days 27–33 have not been started.

## Files Changed

- `frontend/src/context/ModelContext.tsx`
- `Steps/Day-26-mvp-interaction-checkpoint.md`

# Day 52 — Runtime Stabilization and Move Tool (MVP)

**Status:** COMPLETE — committed locally on 2026-10-23 as two implementation commits, **not pushed**:
- `045449d3c221189d3bf9667287715198e2e43d93` "Day 52: Diagnose OpenSCAD failures and handle unsupported AI providers" (parent `52fa8f3`);
- `c0f6b3977a3947c583b3d1a2182e5a2fd2cec653` "Day 52C: Implement persistent wall translation in the viewport" (parent `045449d`).

This record is a separate documentation commit on top of `c0f6b39`.

*Written on 2026-10-23 from the Day 52 Phase 1 / Phase 2 / 52B / 52C reports, the review and test-hardening passes, and the Git history (session record of 2026-10-23). Hashes, parents and numstat were checked against the repository while writing.*

## Sequence

| Step | Kind | Outcome |
|---|---|---|
| Phase 1 | read-only diagnosis | root causes of three user-reported runtime issues |
| Phase 2 | implementation | OpenSCAD failure diagnostics; unsupported-provider handling (commit `045449d`) |
| 52B | read-only investigation | Move was a disabled, unwired toolbar button with no data-model support |
| 52C | implementation | Move MVP end to end (commit `c0f6b39`) |
| Review + hardening | review, then test-only changes | no blockers; `move.mjs` extended from 25 to 29 checks before commit |

## Phase 1 — Diagnosis (read-only)

User-reported symptoms: editing Thickness "reset to 1111"; the panel showed "Save failed: Geometry could not be regenerated; the change was not saved. (OpenSCAD did not produce new geometry)"; AI requests failed for lack of an Anthropic API key.

**Confirmed findings:**

1. **Geometry failure — configuration.** The backend had been started without `OPENSCAD_BINARY`, so it ran the default `openscad`, which is not on `PATH` on this machine; OpenSCAD is installed at `C:/Program Files/OpenSCAD/openscad.exe`. Nothing loads `backend/.env` (`python-dotenv` is listed in `requirements.txt` but never imported). Reproduced with the real engine: `'openscad'` → `FAILED` ("binary 'openscad' not found … [WinError 2]"); the absolute path → `SUCCESS`.
2. **The reason was discarded.** `CLIExecutor` captured it, `OpenSCADEngine` returned it as `RenderResult.error`, and `WallGenerator.generate` dropped it without logging; `EntityStore` then reported only the generic "did not produce new geometry". This is the Day 49 audit's "OpenSCAD failure reason not logged" gap.
3. **"Reset to 1111" was not a separate defect.** The stored value really was 1111. On a failed save, `useModel` reverts the optimistic value and refetches, so the panel correctly showed the committed value.
4. **AI provider.** `LLM_PROVIDER` defaults to `anthropic`; `ANTHROPIC_API_KEY` is read only from the process environment. The Anthropic/OpenAI branch of `LLMClient.complete` ended in `raise NotImplementedError`, which `AIChatService` does not catch, so **setting a key would have produced HTTP 500**. The missing-key message pointed at `backend/.env`, which is never loaded. Ollama is the only implemented provider.

## Phase 2 — Diagnostics and provider handling (commit `045449d`)

| File (numstat) | Change |
|---|---|
| `backend/services/geometry/wall_generator.py` 10/0 | On a failed render, log a warning with the wall guid, render status and `RenderResult.error`, then return the previous geometry as before. The failure contract (generic 502, model unchanged) is unchanged. |
| `backend/main.py` 11/0 | At startup, warn when `shutil.which(OPENSCAD_BINARY)` resolves nothing. `shutil.which` checks a path with a directory component directly and looks a bare command name up on `PATH`. |
| `backend/services/llm/llm_client.py` 20/4 | Non-Ollama providers raise `LLMClientError` ("The '<provider>' LLM provider is not implemented yet. Ollama is the only provider currently supported; start the backend with LLM_PROVIDER=ollama."), so the chat answers with a graceful reply instead of HTTP 500. The missing-key message names the provider's variable and says `backend/.env` is not loaded automatically. |
| `backend/tests/test_property_transaction.py` 22/0 | `test_missing_binary_reason_is_logged_and_edit_rejected`; the render warning is muted in `setUp` like the existing `logger.exception`. |
| `backend/tests/test_llm_intent_parsing.py` 47/0 | `UnimplementedProviderTest`: provider with a key, missing-key guidance, `/api/ai/prompt` answers 200. |

**Not changed (by decision):** default provider, Anthropic implementation, automatic `.env` loading, README setup text, the 502 detail text.

**Validation (2026-10-23):**
- New tests fail against the HEAD versions of the three source files (scratch copy) and pass against the patched ones.
- Startup check: `openscad` (not on PATH) → warning; `cmd` (on PATH) → none; absolute existing path → none; absolute missing path → warning.
- Isolated live backend on :8052 (scratch model copy and STL directory, real OpenSCAD): Thickness 1111 → 200 returned 200 with a new STL (bounds 1111 × 200 × 1111), consistent in memory, on disk and from `GET /api/model`.
- Same backend with `LLM_PROVIDER=ollama` and the real `llama3.2:3b`: "Make the selected wall 2800 mm high" produced a proposal in 12.4 s; `/api/ai/confirm` applied it and regenerated the wall.
- **Intermediate tree of `045449d`** (tree `9e1c35ef…`, exported to a scratch directory before committing): **backend 118 passed, 0 skipped**. It contains no Move code.

## Runtime configuration

The backend reads configuration only from its process environment; `backend/.env` is **not** loaded. For a working local backend:

| Variable | Value | Why |
|---|---|---|
| `OPENSCAD_BINARY` | `C:/Program Files/OpenSCAD/openscad.exe` (machine-specific; not in source) | `openscad` is not on `PATH` here |
| `LLM_PROVIDER` | `ollama` | the only implemented provider; the default `anthropic` now fails gracefully |
| `MODEL_JSON_PATH`, `SCAD_TMP_DIR` | a disposable copy and directory | for manual testing without touching `backend/data/model.json` |

## 52B — Move investigation (read-only)

Move was "a toolbar control not wired to the viewport", and nothing behind it existed:
- `Toolbar.tsx` declared Move with `enabledInMvp: false`; the active tool was local state that never left the toolbar.
- `SceneManager.applyTransform()` threw "not implemented"; left-drag was deliberately unbound.
- `IfcEntity` had no position; `wall.scad.j2` always rendered at the origin and `WallGenerator` replaced bounds with the STL's, so every regeneration returned the wall to (0, 0, 0).
- No API, persistence or tests for movement. Rotate and Draw have the same gap and are out of scope.

## 52C — Move MVP (commit `c0f6b39`)

### Placement convention (MVP, not IFC placement)

Wall position is stored as a property set **`OpenBIM_Placement`** with **`X`, `Y`, `Z` in millimetres**: the world position of the wall's minimum corner.

- This is an **MVP convention**. It is **not** IFC `ObjectPlacement` / `IfcLocalPlacement`: there is no placement hierarchy, no relative placement to the storey, no rotation, and storey elevation is ignored. **Full IFC placement is not supported.**
- The set is optional: a missing set or coordinate means 0, so existing walls stay at the origin with no data migration.
- It is not part of the AI command contract; the AI cannot move walls.

### Backend

| File (numstat) | Change |
|---|---|
| `services/ifc/geometry_inputs.py` 43/1 | `PLACEMENT_PSET`, `PLACEMENT_INPUTS`, `MAX_COORDINATE_MM = 100_000`; `coordinate_error` (finite number, not boolean, \|v\| ≤ 100 000 mm; zero and negative allowed); `geometry_value_error` picks the coordinate or dimension rule; `drives_geometry` includes placement. `GEOMETRY_INPUTS` (the required dimensions, and the AI contract's parity source) is unchanged. |
| `services/ifc/entity_store.py` 20/2 | Validates edits with the matching rule; a stored placement value that is present but invalid blocks regeneration (422). Missing placement is not required. |
| `services/geometry/wall_generator.py` 31/3 | Reads X/Y/Z (missing = 0, invalid = no render) and passes `x`, `y`, `z` to the template. |
| `services/openscad/component_registry.py` 5/0 | `x`, `y`, `z` parameters, default 0, so `POST /api/scad/render` keeps working. |
| `templates/wall.scad.j2` 7/2 | `translate([x, y, z]) wall(...)`. |

**Persistence:** placement edits go through the unchanged atomic path `POST /api/properties/{guid}` → `EntityStore.set_prop()`: applied to a copy, regenerated, saved together, or rejected with nothing changed. No new endpoint or request/response schema.

**World-space bounds:** `GeometryRef.bounds` still comes from the STL, which is now translated, so bounds are world coordinates (`min` = placement). A dimension edit keeps the placement.

### Frontend

| File (numstat) | Change |
|---|---|
| `context/ViewportContext.tsx` 51/2 | `activeTool`, `moveStatus`; `PLACEMENT_PSET`, `placementCoordinate`, `moveTarget` (exactly one selected `IfcWall` with geometry and finite X/Y). |
| `components/layout/Toolbar.tsx` 36/8 | Move enabled only for an eligible selection; falls back to Select when the selection stops being eligible; shows the Move status message. Rotate and Draw stay disabled. |
| `components/viewport/SceneManager.ts` 117/2 | three.js `TransformControls` (no new dependency): translate mode, world space, **X and Y arrows only (Z hidden)**, `translationSnap = 1` (whole mm), orbit disabled while dragging. **Single-axis only:** the XY-plane handle never moves the wall and is reported as rejected. The gizmo re-attaches when the mesh is replaced and detaches before a mesh is disposed (a mesh swap mid-drag cancels the drag without saving). |
| `components/viewport/Viewport.tsx` 74/0 | Attaches the gizmo for the eligible wall (not while saving); commits a drag as `editProperty(guid, "OpenBIM_Placement", axis, current + delta)`, the same `useModel` path as the Properties panel; reports "Saving move…", "Moved: X = … mm." or "Move not saved: …". A press that starts a gizmo drag is never a selection click (`isMoveDragging()` guard). |

On success the regenerated STL replaces the mesh at the new position. On failure `useModel` reverts and refetches, and the old mesh is reloaded.

## Validation

**Final, after both commits (2026-10-23 16:44–16:45, HEAD `c0f6b39`; all suites on temporary model and STL paths):**

| Command | Result |
|---|---|
| `backend/`: `OPENSCAD_BINARY=… venv/Scripts/python.exe -B -m unittest discover -s tests -v` | **128 passed, 0 skipped** (118 + 10: nine `PlacementEditTest` tests and one real-OpenSCAD placement test; two AI-contract rejection cases were added as subtests) |
| `frontend/`: `node_modules/.bin/tsc --noEmit -p tsconfig.json` | exit 0 |
| `frontend/`: `node --test src/components/aichat/aiMessages.test.ts` | 8/8 |
| `frontend/`: `node e2e/move.mjs` | **29/29** |
| `frontend/`: `node e2e/workflow.mjs` | 84/84 |
| `frontend/`: `node e2e/ai-workflow.mjs` | 69/69 |

**Backend coverage (`PlacementEditTest`, `RealOpenSCADEditTest`):** placement edit regenerates at the new position; zero, negative and ±100 000 mm accepted; invalid coordinates (booleans, strings, containers, out of range, NaN/±Infinity) rejected with 422 and nothing changed; regeneration failures on a placement edit (missing binary, FAILED render, render raising) rejected atomically with no pset created; dimension edits keep the placement; walls without placement stay at the origin; invalid stored coordinate blocks regeneration; placement is not AI-editable; the real OpenSCAD STL is translated (X 2000, Y −1500) and keeps its position after a Length edit.

**Browser coverage (`frontend/e2e/move.mjs`, 29 checks, two seeded walls):**
- Move availability: none selected, storey, one wall, two walls (falls back to Select).
- Real X-axis drag: orbit disabled and X-only preview during the drag; exactly one POST (`OpenBIM_Placement.X`, whole mm); new STL at world bounds; viewport mesh replaced; selection kept; Properties panel and saved model show the new X; the other wall unchanged.
- **G0/G1 (added in hardening):** a stationary click on the gizmo where `pick()` finds no wall keeps the selection and sends no POST. A scratch mutant with the guard disabled made G1 fail (selection cleared).
- XY-plane drag: message, no POST, wall unchanged.
- Failed save: an **injected** 502 (the browser answers the POST; the backend is not called) reverts the wall and the panel.
- Reload: wall redrawn at the saved position; **R3 (added in hardening):** the Properties panel shows the persisted X.
- Select mode: clicking a wall in the viewport still selects it.
- Hardening also moved the D0 canvas precondition before its drag and added F0 for the failure drag.
- No unexpected console, HTTP or backend errors; `backend/data/model.json` SHA-256 unchanged.

## Known limitations (accepted for the MVP)

- `OpenBIM_Placement` is a property set, not IFC `ObjectPlacement`; no rotation; storey elevation ignored.
- **Snap-back while saving:** the optimistic update triggers the existing full mesh reload from the old STL, so the wall appears at its old position for about 0.5–1 s before the regenerated STL arrives (identified by code inspection; not measured). Root cause predates Day 52 (all meshes reload on any entity change); to be fixed before release, outside the 52C files.
- Move errors appear in the toolbar status, not the Properties panel's error banner (`useModel` state is per component). A Properties-panel edit is not blocked while a move is saving.
- No Escape-to-cancel; a mesh swap during a drag cancels it silently. Double-click on the gizmo can still select/focus the wall under it.
- The frontend does not check the ±100 m bound; the backend's 422 is shown instead.
- Each move leaves another STL file in the STL directory (existing behaviour).
- Geometry carries the documented ASCII-STL rounding (`Steps/Day-45-48-stl-precision.md`); stored coordinates are exact, and whole-mm moves were exact in the tests.
- The browser 502 case is injected; real backend failure handling is covered by the backend tests only.
- The 502 detail shown in the UI is still generic; the real reason is in the server log.
- README still says `cp .env.example .env`, although `.env` is not loaded.

## Commit procedure

- **Commit 1** staged `main.py`, `llm_client.py` and `test_llm_intent_parsing.py` by path. For the two files holding both phases, Phase-2-only versions were built as the HEAD blob plus only the Phase 2 hunks, checked in both directions (HEAD → Phase 2 = exactly the Phase 2 hunks; Phase 2 → working = only 52C), and staged with `git hash-object -w` + `git update-index --cacheinfo` (blobs `2ad8490a…` `wall_generator.py`, `d94e107d…` `test_property_transaction.py`). The committed tree equals the tested intermediate tree `9e1c35ef…`.
- **Commit 2** staged the twelve remaining Day 52C paths by path. Afterwards HEAD differed from the working tree only in `backend/data/model.json`.
- **Left untouched throughout:** `backend/data/model.json` (modified, never staged; its SHA-256 changed during the day from `23de08c2…` to `fff472db…` at 15:31, attributed to the user's own app session (no test wrote it; all tests used temporary models), and stayed `fff472db…` through every test run and commit) and the untracked `backend/evaluation/`.

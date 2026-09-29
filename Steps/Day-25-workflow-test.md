# Day 25 — Repeatable Workflow Test

**Status:** COMPLETE

**Checkpoint:** `Day 25: Add repeatable wall-edit workflow tests` (code, tests and this record in one commit)

## Objective

Establish a minimal, repeatable end-to-end test of the already-built MVP wall-edit workflow:

select wall → edit Length / Height / Thickness → property update → geometry regeneration → OpenSCAD → STL → GeometryRef update → viewport replaces old mesh → selection remains correct → double-click focus remains correct.

Validation/reproducibility only; no feature changes.

## Starting Point

- No test infrastructure existed in the repository (no test directory, no pytest, no frontend test runner).
- Day 22–24 browser validation scripts lived only in a session scratchpad and were never committed.
- The model file location was hard-coded, so any real browser edit would write to `backend/data/model.json`.

## Implementation

- `backend/core/config.py`: optional `MODEL_JSON_PATH` environment override for the model file. When unset or empty, the default `backend/data/model.json` is used exactly as before.
- `backend/tests/test_workflow.py` (Python `unittest`, no new dependencies):
  - Runs the real application lifespan (EntityStore load, OpenSCADEngine wiring) through FastAPI's test client against a temporary model and a temporary STL directory.
  - Seeds a realistic wall (5000 × 3000 × 200 mm).
  - Edits Length → Height → Thickness, twice (round 1: 6000 / 3200 / 250; round 2: 4500 / 2800 / 300).
  - Per edit: HTTP 200 and `success: true`; no `geometry_warning`; new unique `/geometry/<uuid>.stl` URL; STL file exists and fetched bytes match it; STL bounds parsed independently of the backend's `StlParser` match the requested dimensions; GeometryRef bounds match; temporary model on disk and `GET /api/model` agree.
  - Also tests that `MODEL_JSON_PATH` falls back to the default when unset/empty and overrides when set.
  - Skips with a clear message if the OpenSCAD CLI cannot be found.
- `frontend/e2e/workflow.mjs` (Node 24 built-ins + raw Chrome DevTools Protocol, no new dependencies):
  - Refuses to start if port 8000, 5199 or 9333 is in use.
  - Seeds a temporary model with a realistic wall whose starting geometry is a real STL, starts its own backend on it, Vite on :5199 and headless Chrome.
  - Verifies the backend and Vite proxy serve only the temporary model before any interaction.
  - Drives the real UI: clicks the wall where it is drawn (point verified to be the canvas), types each value into the Properties panel input and presses Enter, double-clicks the wall after each round.
  - Observes the DOM (Properties panel, Model Tree), network (POST response body, STL fetch), canvas screenshot pixels (selection colour, wall centred after focus), console/exception/log events and the backend log.
  - Checks the SHA-256 of `backend/data/model.json` before and after.
  - Optional `E2E_SCREENSHOT_DIR` saves the analysed canvas screenshots.

## Files Changed

- `backend/core/config.py`
- `backend/tests/__init__.py`
- `backend/tests/test_workflow.py`
- `frontend/e2e/workflow.mjs`
- `Steps/Day-25-workflow-test.md`

## Commands

    cd backend  && venv/Scripts/python.exe -m unittest discover -s tests -v
    cd frontend && node e2e/workflow.mjs        # the dev backend on :8000 must be stopped
    cd frontend && npx tsc --noEmit -p tsconfig.json

## Validation

- Backend: `Ran 2 tests … OK`. The real OpenSCAD pipeline was exercised for all 6 edits.
- Negative OpenSCAD control: with `OPENSCAD_BINARY` pointing at a non-OpenSCAD executable, all 6 edits failed on `geometry_warning` and the final distinct-STL count failed (`1 != 7`). With a missing binary the workflow test skipped with an explanatory message.
- Browser: **74/74 checks passed**, on **three consecutive complete runs**.
  - Selection: clicking the wall selected it in the Properties panel and Model Tree; 0 selection-colour pixels before, 110,175 after.
  - Each of the 6 edits: backend confirmed the value; no geometry warning; new unique STL; GeometryRef bounds correct; browser fetched the STL (200).
  - Mesh replacement: new mesh is the real STL (`BufferGeometry`, 36 vertices, bounds equal to the requested dimensions), not the box placeholder; old mesh removed from the scene; one mesh remains.
  - Selection/highlight preserved after every replacement (`selected`, `#ff8c1a`, wall drawn in the selection colour, panel and tree still show the wall).
  - Double-click focus: camera target exactly equals the regenerated mesh's centre (`[3000,125,1600]` after round 1, `[2250,150,1400]` after round 2); wall centred on screen; selection kept.
  - No unexpected console errors, exceptions, HTTP failures or backend errors.
  - Repeatability: 6/6 edits passed, 7 distinct STL URLs.
- `npx tsc --noEmit -p tsconfig.json`: passed.
- `backend/data/model.json` SHA-256 unchanged across all backend and browser runs: `168b3bcf25875606a556ddcee88b1610ec1ccb2cc21547b837d0fad29eae448d`.
- Default `MODEL_JSON_PATH` behaviour: with the variable unset, the backend served `wall-001` from `backend/data/model.json` (read only; hash unchanged).

## Findings During Validation

The first browser run failed 3 checks. All were diagnosed from saved screenshots as test-side issues, not application defects:

- Focus centring (2 checks): the camera target was already exact, but the pixel classifier missed the wall's dark front face (the HemisphereLight lights from +Y, so in the Z-up scene the camera-facing face gets the ground colour), skewing the on-screen centroid. Classification was made hue-based.
- Error check (1 check): Chrome's automatic `/favicon.ico` request returns 404 (the app ships no favicon). Only that request is excluded, with the reason documented in the script.

## Browser Test Limitations

- Distinguishing the real STL mesh from the box placeholder is not observable on screen (for a wall both are identical boxes), and the app exposes no other observation point. The test therefore reads SceneManager state read-only via the `<canvas>`'s React fiber → `Viewport` hooks → the ref holding the SceneManager. It reads only mesh geometry type, vertex count, bounds and uuid, the highlight map, whether the old mesh is still in the scene, mesh count and the camera target. The application does not depend on the test.
- Needs exclusive use of port 8000, because `vite.config.ts` fixes the proxy target.
- Defaults to Windows paths for Chrome and OpenSCAD (override with `CHROME_PATH`, `OPENSCAD_BINARY`); software WebGL; fixed 1600×900 window; single wall.
- Screenshot colour checks depend on the current lighting and highlight colour.

## Deliberately Deferred Observations

- `GridHelper` is not rotated for the Z-up scene, so the grid is a vertical plane at y = 0 that overlaps the wall's front face.
- The viewport canvas (923 px tall) extends past a 900 px window.
- Generated STL files accumulate and are never cleaned up.
- `backend/.env.example` does not yet mention `MODEL_JSON_PATH`.
- Existing semantics unchanged: a property is saved even if geometry regeneration fails (Day 20/24 known limitation).

## Architecture Notes

No changes to GeometryFactory, OpenSCADEngine, SceneManager, Viewport, ModelContext, API schemas or AI functionality. The save order (property in memory → geometry regeneration → persistence) is unchanged. The only application change is the opt-in model-path override.

## Git

The pre-existing `backend/data/model.json` working-tree modification remains intentionally uncommitted.

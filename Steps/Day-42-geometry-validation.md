# Day 42 — Geometry and Viewport Validation

**Status:** VALIDATION ONLY — no application code changed; no commit.

*Written retrospectively on 2026-10-17 (Day 51), from the Day 42 report (session record of 2026-10-16) and the scripts, JSON results and logs in that session's scratchpad (`day42-geometry/`), outside the repository. All results are **historical**.*

## Objective

Check that a property edit produces exactly the expected geometry, from the stored value through the STL file to the viewport mesh, including failures and recovery.

## Method

- **Isolation:** a scratch backend on scratch copies of `model.json` and the STL directory; Vite on :5173; headless Chrome. The real `model.json` and `C:\tmp\openbim-scad` were not written.
- **API script (`geometry-api.mjs`, 33 edits):** compared the requested value, the HTTP response, the persisted scratch model and the STL file itself, parsed independently (vertex min/max).
- **Browser script (`geometry-ui.mjs`, 16 edits):** mesh count, disposal and detachment of the old mesh, mesh bounds against stored bounds, selection and highlight, panel values, four window sizes.
- **Failure test:** the scratch backend restarted with an invalid OpenSCAD path, then with the valid one.

## Results

| Scenario | Result |
|---|---|
| Whole-mm and simple decimal edits (5000 × 200 × 3000; 12000 × 4500; 50, 1, 0.5 mm; successive heights; reverts; 100000 mm) | **Exact.** STL max = [L, T, H], min = [0, 0, 0]; stored bounds = STL; X = Length, Y = Thickness, Z = Height; one new STL and URL per edit |
| Rejections (0, −1, `"3000"`, `true`, `null`, 1e9, 100000.001) | 422; nothing persisted; no STL written |
| Non-geometry edit (`FireRating`) | 200; no regeneration |
| 16 browser edits | one mesh each time; old mesh disposed; bounds match; still selected and orange |
| OpenSCAD missing (API ×2, UI ×1) | 502; nothing persisted; no STL; the UI reverts and the mesh keeps the last valid geometry |
| Recovery with the valid binary | next edit 200 with a correct STL |

**Tests (historical):** backend 114 OK (including `RealOpenSCADEditTest`); `tsc` exit 0; `aiMessages` 8/8; `workflow.mjs` 84/84 ×3; `ai-workflow.mjs` 5 × 69/69 and **1 aborted** (F2).

## Findings

- **F1 — precision defect (confirmed).** Dimensions with more than 6 significant digits are rounded in the geometry. Stored 1234.567 became 1234.57 in the STL and stored bounds; 12345.678 became 12345.7. The `.scad` source keeps full precision; OpenSCAD 2021.01's ASCII STL export writes 6 significant digits (reproduced with a bare `cube()` outside the app). Whole millimetres up to 100 m are exact. Investigated further on Days 45–48 (`Steps/Day-45-48-stl-precision.md`).
- **F2 — intermittent e2e timeout (suspected, cause unknown).** `ai-workflow.mjs` aborted in 2 of 9 runs across Days 41–42: once at "manual-edit mesh" (Day 41) and once at "seed STL in viewport" (Day 42), both 30-second waits. Followed up on Days 43–44 (`Steps/Day-43-44-e2e-timeout-diagnostics.md`).
- **F3 — OpenSCAD failures are not explained (confirmed).** The backend log shows only the 502 access line; the user sees a generic message. Already deferred since Day 28; still open.
- **F4 — a rejected or failed edit rebuilds the mesh (observation).** The frontend refetches the model and recreates the mesh from the **same** STL. The geometry stays correct, but the reload is unnecessary.

**Previously known items seen again:** a stale validation message on another field (Day 38 D4); selection lost on reload; STL files never removed (24 new files from 33 API edits, one referenced); no auto-fit (a 12 m wall shows 4 of its 8 corners at the default camera).

**Policy, not defects:** 0.5 mm (any value above 0) is accepted; values above 100 m are rejected, as designed.

## Status on 2026-10-17

F1 is documented as a tolerance policy (Option A), not fixed in code. F2's root cause is unresolved. F3 and F4 are open.

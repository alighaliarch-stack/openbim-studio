# Day 39 — AI Composer Layout Fix

**Status:** COMPLETE — committed `df7c9ef` "Day 39: Keep the AI composer visible at laptop resolutions" (2026-10-17), pushed to `origin/main` on 2026-10-17.

*Written retrospectively on 2026-10-17 (Day 51). The implementation is in the repository. The measurements are **historical**: they come from the Day 39 report (session record of 2026-10-15) and the scripts and JSON results in that session's scratchpad (`day39-layout/`), outside the repository.*

## Objective

Fix Day 38 finding D1: at 1440×900 and 1280×720 the AI chat composer sat below the bottom of the window, so the page had to be scrolled to reach it.

## Root cause

The Day 38 guess (the toolbar height) was wrong. The cause was a feedback loop in the grid:

1. `.app-shell__body` is a grid with no explicit row, so its single implicit `auto` row was sized by its content.
2. The viewport `<main>` was not a scroll container, so its minimum height was its content: the canvas (whose natural height equals the drawing-buffer height set by `renderer.setSize()`) plus the unstyled "Loading geometry…" overlay, which sat in normal flow below the canvas.
3. The `ResizeObserver` fed that height back into the canvas, so it could only grow (measured up to 2239 px after a reload at 1920×1080).

## Change (`frontend/src/styles/global.css`, +7/−0)

- `.app-shell__body`: `grid-template-rows: minmax(0, 1fr);` pins the row to the space below the toolbar.
- `.app-shell__viewport`: `overflow: hidden;` keeps the overlay inside its cell.

Both carry "Day-39" comments. No other file changed.

## Validation (historical)

| Window | Before: page height / composer top | After: page height / composer |
|---|---|---|
| 1440×900 | 984 / y=954, not visible | **900** / y=870–891, visible |
| 1280×720 | 804 / y=774, not visible | **720** / y=690–711, visible |
| 1920×1080 | 1143 / y=1113, not visible | **1080** / y=1050–1071, visible |
| 1024×700 | 742 / y=712, not visible | **700** / y=670–691, visible |

- **Resize and load:** shrinking from 1920×1080 to 1024×700 without reloading worked, and the viewport height stayed constant during loading.
- **Long conversation:** with 16 messages, the messages scrolled inside their own area and the composer stayed visible.
- **Tests:** `tsc` exit 0; `e2e/workflow.mjs` 84/84; `e2e/ai-workflow.mjs` 68/68; no console errors apart from the existing `favicon.ico` 404.
- **Re-verified on 2026-10-17 (Day 50B), at HEAD `8561338`:** `workflow.mjs` 84/84; `ai-workflow.mjs` 69/69. The layout measurements were not repeated.

## Remaining limitations

- The tree panel shows a horizontal scrollbar (the long "Ground Floor" row). This is pre-existing and was previously hidden below the window.
- The "Loading geometry…" overlay is still never visible; it is now clipped inside the viewport cell.
- The chat does not auto-scroll to the newest message.

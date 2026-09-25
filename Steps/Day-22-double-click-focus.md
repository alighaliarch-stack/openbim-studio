# Day 22 — Double-click Focus

**Status:** COMPLETE — verified existing functionality

**Validation date:** 2026-09-24
**Git HEAD at validation:** `ae88ecc Day 21: Preserve selection highlight across mesh replacement`

## Historical fact

- Double-click focus was already implemented before Day 20–21.
- It exists through `Viewport.tsx` `onDoubleClick()` (`handleDoubleClick`) and `SceneManager.focusOn()`.
- Git history (`git log -S focusOn`, `git log -S onDoubleClick`) attributes the implementation to checkpoint `82fc58b`, "Checkpoint before six-month PhD review".
- Therefore Day 22 is a validation/hardening milestone, not a newly implemented feature.

## Validation

- Final validation run: 50/50 checks passed.
- `npx tsc --noEmit` passed.
- Double-click selects and focuses the wall.
- Camera framing and distance are correct.
- Orbit target moves to the wall centre.
- Right-drag orbit, middle-drag pan, wheel zoom and left-drag behavior remain correct.
- Empty-space double-click does not move the camera.
- Focus works while the placeholder mesh is displayed.
- Focus works after regenerated STL replaces the placeholder.
- Day 21 selection highlight remains intact after STL replacement.
- No application console errors were observed; only the existing favicon 404 was reported.
- No source files were changed during validation.

## Limitations / observations

- One initial headless-browser run had 39/50 checks pass; two subsequent runs passed all 50 checks.
- Verified cause (identified during Day 23): the initial 39/50 result was caused by the test coordinates, not an application or browser startup problem.
  - With the 10000 mm wall at the default camera framing, the wall's centre was positioned underneath the Properties panel.
  - The test clicked that covered point, so the click landed on the panel rather than the visible viewport wall.
  - Raycasting itself was not restricted to the canvas, so the test could still report a hit.
  - Subsequent runs clicked a visible point on the wall and passed (the Day 22 reruns started from the regenerated 6000 mm wall, whose centre was on the canvas; the Day 23 harness now selects a visible in-canvas wall point explicitly).
- Therefore this was a test-script/coordinate issue, not an application defect.
- Known narrow/tall viewport fitting edge case remains intentionally deferred.
- The wall geometry is currently generated at the origin; multi-object placement remains future work.

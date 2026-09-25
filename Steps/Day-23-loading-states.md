# Day 23 — Loading States

**Status:** COMPLETE

## Objective

Verify and improve useful loading feedback during property editing and geometry regeneration.

## Implementation

- `PropertyPanel` disables the complete property grid while a save is running (existing `disabled` prop, previously hard-coded to `false`), preventing overlapping edits.
- Geometry-bearing entities display "Saving and regenerating geometry…".
- Non-geometry entities retain "Saving…".
- `useModel` uses a request-id guard (`useRef` counter checked in `finally`) so an older request cannot clear the saving state of a newer request.
- Existing success (`✓`), geometry-warning (`⚠`), and save-failure feedback is preserved; the per-field `…` indicator is unchanged.

## Files Changed

- `frontend/src/components/properties/PropertyPanel.tsx`
- `frontend/src/hooks/useModel.ts`

## Validation

- 41 checks passed in the main validation run.
- 7 checks passed in the regeneration-failure run.
- TypeScript (`npx tsc --noEmit`) passed.
- Overlapping UI edits are prevented: while a save is held, the whole grid is disabled and a second edit attempt sends no request.
- Direct overlapping-request guard test passed: an older request completing does not clear a newer request's saving state.
- Successful regeneration passed: placeholder with new bounds, then regenerated STL.
- Regeneration failure passed: `⚠` shown, grid re-enabled, previous geometry kept.
- API failure/recovery passed: "Save failed" banner, grid re-enabled, optimistic value reverted, next edit succeeds.
- Day 21 selection highlighting remained correct after STL replacement.
- Day 22 double-click focus remained correct.
- FireRating (on a wall: geometry wording, since walls regenerate on every edit) and non-geometry Storey Elevation (generic "Saving…") behavior were verified.
- No unexpected browser console errors were observed (only the favicon 404 and the errors produced by the deliberately injected HTTP 500).

## Deliberately Deferred

- STL loading indicator
- Earlier `✓` timing before final STL visibility
- Backend blocking OpenSCAD call
- CSS styling of status text
- Optimization of unnecessary STL reloads

These are intentionally outside Day 23 scope.

## Architecture Notes

- The backend currently performs the blocking OpenSCAD call in the request path (`async def edit_property` → `EntityStore.set_prop()` → `subprocess.run`), which stalls the event loop, including STL serving, for the duration of each render.
- Mesh/STL loading does not yet expose a shared loading state; `useModel` saving state is local to `PropertyPanel`, and `SceneManager` has no load-complete signal.
- These are future architectural/performance considerations, not Day 23 defects.

## Git State

Current HEAD: `3f92a83 Day 22: Verify double-click focus`

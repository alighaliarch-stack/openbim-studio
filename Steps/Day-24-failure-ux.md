# Day 24 — Failure UX

**Status:** COMPLETE

**Code checkpoint:** `9dd6771 Day 24: Harden failure UX and recovery`

## Objective

Harden the user experience and recovery behavior for property-edit failures without redesigning the backend or geometry pipeline.

## Implementation

- `useModel.ts` now converts technical failure conditions into actionable user-facing messages:
  - backend unreachable: "Could not reach the backend — check that the server is running, then retry."
  - other 5xx: "The server could not save this change."
  - 4xx responses with readable backend details expose the backend's useful error text.
  - unreadable 4xx responses use a generic rejection message.
- Failed optimistic property edits are reverted locally before the existing reload/refetch attempt, so the UI does not retain an unsaved value when the backend is unreachable.
- Feedback is associated with the entity that generated it through `feedbackGuid`.
- `PropertyPanel.tsx` displays save failure, geometry warning, and success feedback only when it belongs to the currently selected entity.
- Day 20 validation behavior is preserved.
- Day 23 loading-state and request-id behavior is preserved.
- Day 21 selection highlighting and Day 22 double-click focus remain intact.

## Files Changed

- `frontend/src/hooks/useModel.ts`
- `frontend/src/components/properties/PropertyPanel.tsx`

## Validation

- Main validation: 43/43 checks passed.
- Regeneration-failure validation: 7/7 checks passed.
- `npx tsc --noEmit` passed.
- Forced 500 tested.
- 404 and 422 backend error details tested.
- Backend-unreachable scenario tested with the backend actually stopped.
- Local value reversion confirmed even when reload also failed.
- Recovery after backend outage confirmed.
- Entity-scoped feedback confirmed.
- Regeneration failure confirmed with old geometry and selection preserved.
- Day 21 selection highlighting regression passed.
- Day 22 double-click focus regression passed.
- Day 23 loading-state and grid-lock regression passed.
- STL-load failure behavior was recorded but intentionally not changed.

## Deliberately Deferred

- ModelTree error-display improvement during backend outage.
- STL-load failure UI.
- Frontend error boundary.
- Server-side type/range validation.
- Surfacing detailed OpenSCAD failure reasons.
- Detecting saved-property/geometry mismatch.
- Backend asynchronous OpenSCAD/job architecture.
- STL reload optimization.

## Architecture Notes

The Day 24 work deliberately remained within the existing frontend state architecture. No changes were made to the backend, geometry generation, SceneManager, Viewport, ModelContext, or STL loading system.

## Git

Code checkpoint:

`9dd6771 Day 24: Harden failure UX and recovery`

The pre-existing `backend/data/model.json` working-tree modification remains intentionally uncommitted.

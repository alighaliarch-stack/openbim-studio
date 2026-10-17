# Day 32 — AI-to-BIM End-to-End Validation

**Status:** COMPLETE — committed `725d259` "Day 32: Validate AI-to-BIM end-to-end workflow" (2026-10-12), pushed to `origin/main` on 2026-10-12.

*Written retrospectively on 2026-10-17 (Day 51), from the Day 32 report (session record of 2026-10-12) and the run logs kept in that session's scratchpad. The scratchpad is outside the repository. Results below are **historical**, as reported on Day 32, unless marked otherwise.*

## Objective

Validate the complete Day 31 workflow in the real application, from wall selection through to the regenerated mesh in the viewport. Fix any defect found, and add a committed browser regression test for the AI path.

## Workflow inspected

The call path was read in code before testing, and it matched `Steps/Day-31-ai-to-property-bridge.md`:

```text
Propose:  AIChatPanel.handleSend → POST /api/ai/prompt → AIChatService.send_prompt
          → validate_ai_edit (contract → provenance → property → selected-target → target)
          → ProposalRegistry (with original_value)
Confirm:  handleApply → POST /api/ai/confirm {proposal_id, selected_guid} → take() (single use, 600 s)
          → execute_ai_proposal → validate_ai_edit → verify_unchanged → execute_command
          → EntityStore.set_prop (atomic) → ParametricGeometry → WallGenerator → OpenSCAD → StlParser
Display:  applyConfirmedEntity → Viewport → MeshLoader → SceneManager.loadMeshFromStl
          (load tokens discard stale loads; the old mesh is disposed; selection highlight kept)
```

## Defect found and fixed: a lost confirmation reported as "Not applied"

- **Symptom:** when the confirm response was lost *after* the backend had committed, the card said **"Not applied"** although the server held the new value (Height 3700). The viewport and Properties panel kept the old value until a manual reload.
- **Root cause:** `AIChatPanel.handleApply` treated every error as a definite rejection and did not reload the model. The manual edit path already reloads after a failure.
- **Classification:** a gap left by Day 31, not a regression. Day 31 guaranteed that a failure is never reported as success; it did not cover a success being reported as a failure.
- **Fix (frontend only):**
  - a 4xx, or a 5xx carrying the backend's structured `ErrorResponse`, is still a definite rejection ("Not applied"). Edits are atomic, so nothing changed;
  - any other failure (a thrown fetch, an unstructured 5xx) sets a new status, **"Outcome unknown"** ("…may or may not have been applied"), and calls the existing `refetch()`.
- Backend, safety checks, `EntityStore`, OpenSCAD and `SceneManager` were not changed.

## New committed test: `frontend/e2e/ai-workflow.mjs`

- **Real:** uvicorn backend, `EntityStore`, the validation chain, the OpenSCAD CLI, Vite, React and three.js in headless Chrome, driven over CDP.
- **Fake:** only the language model. A local HTTP server on port 11535 speaks Ollama's `/api/chat` and returns fixed JSON per step. The real Ollama was not contacted. **The test does not measure language-model behaviour.**
- **Isolation:** a temporary `model.json` and STL directory via `MODEL_JSON_PATH` / `SCAD_TMP_DIR`. The SHA-256 of `backend/data/model.json` is checked before and after the run.
- **Ports:** it needs 8000, 5199, 9333 and 11535 free.

What it checks (68 checks on Day 32):

| Area | Cases |
|---|---|
| Successful edits | "3.2 m high", "4 m long", "250 mm thick": a proposal card first, nothing changed before Apply, a confirm body of only `{proposal_id, selected_guid}`, persistence, a new STL with the bounds read independently from the file, the old mesh disposed, the selection kept |
| Rejections | no selection; wrong target; door; unsupported property; no / two / `ft` / relative quantity; 0, −3 m, NaN, Infinity, 150 m, 3200 m |
| Concurrency and state | selection changed after proposing (Apply disabled); stale proposal (C7b); used proposal; Dismiss; synchronous double click (one confirm); 5 concurrent confirms (`[200, 404, 404, 404, 404]`); out-of-order STL loads |
| Failures and recovery | response lost after commit (C11); backend down (C12a); backend restarted (C12b); invalid STL url; OpenSCAD missing (real 502); persistence failure (real, read-only model file → 500); final reload agreement |
| Hygiene | no unexpected browser errors (K); `model.json` untouched (M) |

The 600 s proposal expiry is not tested in the browser; backend tests cover it with a controlled clock.

## Results (historical, reported on Day 32)

| What | Result |
|---|---|
| Backend suite (before and after) | 86 tests, OK |
| `tsc --noEmit` | exit 0 |
| `e2e/workflow.mjs` (manual edit path) | 84/84, before and after the fix |
| `e2e/ai-workflow.mjs` before the fix | 67/68: C11 failed |
| Mutation check (old `catch` block restored) | 66/68: C11 and C12a failed, so the test detects the defect |
| `e2e/ai-workflow.mjs` after the fix | 68/68 in three runs |

- **Measured timings** (single runs, not a benchmark): confirm round trip 114–233 ms; Apply click to new mesh visible 187–402 ms.
- **Not mentioned in the Day 32 report.** Two development runs (scratch logs `run3.log` and `ai-workflow-final.log`) ended 67/68. Check K flagged `Failed to load model: … GET /model …` and then `… GET /tree failed with status 500`, which the app logs during the deliberate backend-down step. The test's allow-list of expected errors was extended to `GET /(model|tree) failed with status 500` (present in `725d259`). The next two runs passed 68/68. **Run order** (from the scratch log timestamps and the session record): of the three 68/68 runs, the first (`ai-workflow-run1.log`) used an earlier version of the test that did not yet allow `GET /tree`. Only the last two (`ai-workflow-final-1.log`, `ai-workflow-final-2.log`) ran after the final recorded edit to the test, the allow-list change that `725d259` contains. No hash of the test file was recorded at run time, so it is not proven that those runs used a byte-identical copy of the committed file.
- **Re-verified on 2026-10-17 (Day 50B):** the current version of this test passed 69/69 on HEAD `8561338`. It has 69 checks (Day 40 added P0) and the Day 44 timeout diagnostics.

## Limitations

- The fake language model proves the deterministic pipeline only. There is no measurement of model accuracy and no research validation.
- "Outcome unknown" does not resolve itself; the user has to look at the reloaded model. The status has no dedicated style.
- A failed persistence (C14) leaves one unreferenced STL in `SCAD_TMP_DIR`.
- `ai-workflow.mjs` copies the CDP and scene-probe helpers from `workflow.mjs` rather than sharing them.
- Known limitations unchanged: selection is client-reported; proposals live in memory only; OpenSCAD runs synchronously inside an async route; every model change reloads all meshes; all walls are generated at the origin.

## Files changed (Day 32, `725d259`)

- `frontend/src/components/aichat/AIChatPanel.tsx` (`isDefiniteRejection`; "unknown" status plus `refetch()`)
- `frontend/src/components/aichat/ChatMessage.tsx` ("Outcome unknown" label)
- `frontend/src/types/commands.ts` (`"unknown"` in `ProposalStatus`)
- `frontend/src/hooks/useModel.ts` (exports the existing `isStructuredError`)
- `frontend/e2e/ai-workflow.mjs` (new)

Not changed: backend code, `backend/data/model.json`.

# Days 43–44 — Intermittent E2E Timeout: Diagnosis and Diagnostics

**Status:**
- **Day 43:** DIAGNOSIS ONLY — no repository change. Root cause **unresolved**.
- **Day 44:** COMPLETE — committed `8561338` "Day 44: Print diagnostics when an AI workflow wait times out" (2026-10-17), pushed to `origin/main` on 2026-10-17. Test tooling only; **the timeout itself is not fixed.**

*Written retrospectively on 2026-10-17 (Day 51), from the Day 43 and Day 44 reports (session record of 2026-10-16) and the files in that session's scratchpad (`day43-diag/`, `day44-diagnostics/`), outside the repository. Results are **historical** unless marked otherwise.*

## Background

`frontend/e2e/ai-workflow.mjs` aborted in 2 of 9 runs on Days 41–42, each time on a 30-second wait for an STL mesh (`Steps/Day-42-geometry-validation.md`, F2). The test printed nothing beyond "timed out waiting for: …".

## Day 43 — diagnosis

**What the failing waits require (from the code):**
- **"seed STL in viewport":** Wall A's mesh becomes `BufferGeometry` after page load.
- **"manual-edit mesh":** Wall A's mesh has the new dimensions after the server confirmed the edit.
- A stale mesh cannot satisfy either wait.
- The page was responsive during both failures: each reported the wait's own timeout, not a CDP evaluation timeout.

**Confirmed gaps (neither is proven to be the cause):**
1. `SceneManager.loadMeshFromStl()` has an empty error callback, so a failed STL load leaves the placeholder box silently.
2. The test printed no evidence on timeout.

Other facts:
- every entity change reloads every STL;
- `StrictMode` doubles the initial refetch;
- this test seeds Wall B with a deliberately missing STL (a 404 on every reload).

**Reproduction attempt:** an instrumented scratch copy of the test (`ai-workflow.instrumented.mjs`; same assertions, flow and timeouts, plus a timeline and a dump on timeout) **passed 10/10**, each run taking 78–118 s.

| Wait | Max over 10 runs | Median |
|---|---|---|
| seed STL in viewport | 568 ms | 538 ms |
| manual-edit mesh | 802 ms | 117 ms |

- 261 geometry requests in total; the slowest took 1.22 s.
- Healthy runs have a 37–50× margin to the 30 s timeout, so a failure would be a stall, not slowness.

**Classification:** no confirmed application or test-synchronisation defect. Remaining hypotheses: an STL request left pending through the Vite proxy; a silent STL load or parse error; the viewport never rendered; a host or resource stall.

## Day 44 — diagnostics added to the test

**Change (`frontend/e2e/ai-workflow.mjs`, +100/−5 over the Day 40 version):**
- `waitFor` records its start. On timeout it calls `onWaitTimeout(label, elapsedMs)` inside `try/catch`, then throws the **unchanged** error. If the diagnostics fail, one `DIAG diagnostics failed` line is printed and the original error is still thrown.
- **`printTimeoutDiagnostics()`** prints bounded `DIAG` lines (15 requests, 15 console lines, 15 Vite lines, 25 backend lines, 300 characters each):
  - `/geometry` requests: open and finished, status, age and duration, using new `sentAt` / `endedAt` fields;
  - the viewport state: each mesh as placeholder or STL, load tokens, whether the renderer is mounted;
  - browser console warnings and errors (the CDP `Log` domain is now enabled);
  - the Vite stdout/stderr tail, which the test now reads;
  - the backend log tail.
- A `diag` holder fills in as each source becomes available.

**Unchanged:** every assertion, wait condition, timeout and scenario, and all application code.

**Validation (historical):**
- **Forced timeout:** a scratch copy with an impossible 3-second seed-STL wait exited 1 with the original error, printed the `DIAG` block, and still ran its cleanup (check M passed).
- **Normal runs:** `ai-workflow.mjs` 69/69 ×3 with no `DIAG` output; `workflow.mjs` 84/84.

**Re-verified on 2026-10-17 (Day 50B), at HEAD `8561338`:** `ai-workflow.mjs` 69/69, no `DIAG` output.

## Run record since Day 42 (all recorded runs passed)

| Day | Runs | Version |
|---|---|---|
| 43 | 10 | instrumented scratch copy, not the committed file |
| 44 | 3 | Day 44 working version |
| 49 | 2 | Day 44 working version |
| 50B | 1 | committed `8561338` |

No timeout has recurred, so the new diagnostics have not yet captured a natural failure.

## Remaining uncertainty

- Root cause unresolved.
- STL load errors are still silent in the application. A one-line `console.warn` in `SceneManager.loadMeshFromStl()` was proposed on Day 43 but **not implemented**.
- The test now reads Vite's output, enables the CDP `Log` domain and records two timing fields per request. No effect on the checks was observed, but in principle these could change timing slightly.
- Load tokens are read from a TypeScript-private field at runtime. If that field were renamed, the line would print "unavailable" rather than fail.

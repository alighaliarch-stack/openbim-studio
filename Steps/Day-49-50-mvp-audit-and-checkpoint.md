# Days 49–50 — MVP Readiness Audit and Git Checkpoint

**Status:** COMPLETE. Day 49 was a read-only audit. Day 50 created four local checkpoint commits and pushed them, together with Day 37, to `origin/main` (`fc1fa7b..8561338`, non-force, 2026-10-17).

*Written on 2026-10-17 (Day 51), from the Day 49, 50A, 50B, 50C and 50D reports (session record of 2026-10-17), the Git history and the remote-tracking reflog. Commit hashes and the push were verified against the repository during Day 50 and again while writing this record.*

## Day 49 — Baseline and MVP readiness audit (read-only)

**Baseline:**
- **Commits:** HEAD `764db95` (Day 37), one commit ahead of `origin/main` (`fc1fa7b`).
- **Uncommitted but validated:** Days 39, 40, 41 and 44. `frontend/e2e/ai-workflow.mjs` held both Day 40 (+14/−4) and Day 44 (+100/−5) changes. The Day 40-only version had been preserved in the session scratchpad (SHA-256 `556A6867…`).

**Tests (historical, Day 49):** backend 114 OK; `tsc` exit 0; `aiMessages` 8/8; `workflow.mjs` 84/84; `ai-workflow.mjs` 69/69 ×2, no `DIAG` output.

**Known issues reassessed:**

| Issue | Classification |
|---|---|
| Intermittent AI e2e timeout | cause unresolved; diagnostics in place (`Steps/Day-43-44-e2e-timeout-diagnostics.md`) |
| OpenSCAD failure reason not logged | reproducible diagnosability gap: `wall_generator.py` neither logs nor uses `RenderResult.error` (confirmed in code) |
| Stale validation message on another field | reproducible, cosmetic: `InputField` clears its message only for its own changes (confirmed in code) |
| STL files accumulate; mesh rebuilt after a rejected edit | reproducible, low |
| Selection and camera lost on reload; no auto-fit | not implemented |
| AI turns "3.2 m" into 3200 m (rejected safely) | model limitation; now explained to the user; B2 undecided |
| Inputs below 2⁻¹²⁶ mm; translated geometry | outside the precision bound; policy and design decisions |
| Steps documents stopped at Day 31 | documentation gap (addressed by this Day 51 update) |

**Recommendation:** checkpoint the validated work in clean commits first. After that, the smallest worthwhile code fix is logging the OpenSCAD failure reason. **Not implemented.**

## Day 50A — Preflight (read-only)

- Confirmed the Day 40 / Day 44 split of `ai-workflow.mjs` byte for byte:
  - HEAD blob `0d610202…`;
  - Day 40 version blob `8825814c9f70018640c9f3a8fc8387186885499e` (HEAD +14/−4, no Day 44 markers);
  - Day 44 working version blob `7533139f2a6516570520dbcc3b875c5bbc279c89` (Day 40 +100/−5).
- Proposed non-interactive staging of the Day 40 version: `git hash-object -w` followed by `git update-index --cacheinfo`, without modifying the working file.

## Day 50B — Four local checkpoint commits

| Day | Commit | Parent | Paths (numstat) |
|---|---|---|---|
| 39 | `df7c9ef5e4423b2296084dab0b8e2c0995365506` | `764db95` | `frontend/src/styles/global.css` 7/0 |
| 40 | `699263a56aa0bd5f44cc9e87eaefb8abf930e11b` | `df7c9ef` | `frontend/e2e/ai-workflow.mjs` 14/4 (blob `8825814c…`); `AIChatPanel.tsx` 5/2; `ChatMessage.tsx` 4/1; `aiMessages.test.ts` 87/0 (new); `aiMessages.ts` 86/0 (new); `types/commands.ts` 2/0 |
| 41 | `b65e3cbe788c38a485067fcd0fefc04de145f5a8` | `699263a` | `frontend/src/components/viewport/SceneManager.ts` 17/3 |
| 44 | `8561338604f234533d761d358f7cd99ec4c9d885` | `b65e3cb` | `frontend/e2e/ai-workflow.mjs` 100/5 (blob `7533139f…`) |

- **Staging:** explicit paths only. The Day 40 e2e version was staged with `git hash-object -w` (which returned `8825814c…`) followed by `git update-index --cacheinfo`; the working file was not modified.
- **Before each commit:** the staged paths, numstat and blobs were reviewed, and `git diff --cached --check` exited 0.
- **After each commit:** a scripted check confirmed the index was empty, `backend/data/model.json` was unchanged (SHA-256 `23de08c2…be345f`, never staged), `backend/evaluation/` was still untracked with unchanged hashes, the other untracked and historical files were unchanged, and `origin/main` had not moved.

**Tests after the four commits (fresh, 2026-10-17, HEAD `8561338`):**

| Command | Result |
|---|---|
| `backend\`: `.\venv\Scripts\python.exe -B -m unittest discover -s tests -v` | 114 tests, OK |
| `frontend\`: `npx tsc --noEmit -p tsconfig.json` | exit 0 |
| `frontend\`: `node --test src/components/aichat/aiMessages.test.ts` | 8/8 |
| `frontend\`: `node e2e/workflow.mjs` | 84/84 |
| `frontend\`: `node e2e/ai-workflow.mjs` | 69/69, no timeout |

Both e2e scripts ran on temporary models and STL directories. The 118 files in the real STL directory `C:\tmp\openbim-scad` were unchanged.

## Day 50C — Post-checkpoint audit (read-only)

- The chain `fc1fa7b → 764db95 → df7c9ef → 699263a → b65e3cb → 8561338` was verified with full hashes, parents and paths.
- The e2e blob at each commit was confirmed: Day 39 `0d610202…`, Days 40 and 41 `8825814c…`, Day 44 `7533139f…`.
- `origin/main` was still `fc1fa7b`: 5 ahead, 0 behind. The index was empty.

## Day 50D — Push

- **Preconditions, checked before pushing:**
  - branch `main`, HEAD `8561338`, tracking ref `fc1fa7b`;
  - `git ls-remote` showed the remote `main` still at `fc1fa7b`;
  - exactly five commits in the range.
- `git push origin main` (no force): `fc1fa7b..8561338  main -> main`.
- **After the push:** the tracking ref and `git ls-remote` both showed `8561338`, with 0 ahead and 0 behind.

**Left untouched throughout:** `backend/data/model.json` (modified, never staged) and the untracked `backend/evaluation/`. Neither is part of the published checkpoint.

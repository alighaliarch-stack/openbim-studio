# Day 31 — AI-to-Property Execution Bridge

**Status:** IMPLEMENTED — awaiting review (not committed). Base: `49f9718` (Day 29–30 checkpoint). Includes the safety corrections from the Day-31 adversarial review (§7).

Day 31 is the first milestone in which an AI-originated command can change the model. It does so only after an explicit user confirmation, only for the selected wall, and only through the existing Day-27 executor and Day-28 atomic property pipeline.

## 1. Inspection findings (before implementation)

Actual Day 29–30 proposal flow (confirmed in code):

```text
AIChatPanel (selected_guid = useSelection().primaryGuid)
  → POST /api/ai/prompt {prompt, selected_guid}
  → AIChatService.send_prompt
      ContextBuilder.build(selected_guid)        stale GUID → graceful reply, LLM not called
      LLMClient (ollama) → OutputParser
      per command: validate_command → verify_provenance → verify_property → verify_target
  → AIResponse (canonical-mm EditProperty proposals only)
  → AIChatPanel: showed text; ignored EditProperty; still had a legacy
    CreateComponent → POST /api/scad/render branch (never triggered, since
    the server returned no CreateComponent)
```

- `execute_command()` (Day 27) validates, then makes exactly one `EntityStore.set_prop()` call; Day 28 made `set_prop()` atomic (property + regenerated geometry + persistence, or nothing).
- **Where the selection comes from:** the frontend's `SelectionContext` (viewport / Model Tree clicks), sent as `selected_guid`. The backend has **no server-side selection state**. `ContextBuilder` only uses the GUID to describe the selected entity to the LLM. The LLM sees the GUID but cannot set it.

## 2. Implementation and data flow

```text
1. Propose (no mutation)
   POST /api/ai/prompt {prompt, selected_guid}
     → AIChatService → LLM → OutputParser
     → ai_edit.validate_ai_edit()  for every command
     → accepted: canonical-mm proposal stored in ProposalRegistry, with the
                 target property's current stored value and a monotonic timestamp
                 → response carries commands[i] + proposal_ids[i]
     → rejected: reason in text, not returned

2. Confirm (explicit user action: "Apply change")
   POST /api/ai/confirm {proposal_id, selected_guid}      ← no command, no value
     → ProposalRegistry.take(id)          removed first: single use; unknown, used or
                                          ≥ 600 s old → 404 proposal_not_found
     → executor.execute_ai_proposal()
         ai_edit.validate_ai_edit()       all checks again, current model + current selection
         ai_edit.verify_unchanged()       property still has its proposal-time value
                                          (else 422 stale_proposal)
         execute_command()                exactly once
           → validate_command → EntityStore.set_prop()   (Day-28 atomic)
             → ParametricGeometry → GeometryFactory → WallGenerator → OpenSCADEngine → STL
         logger.info("AI property edit applied: …")   only after success
     → 200 {success, text: "Applied: …", entity}
       or 404/422 (rejected) / 422/502/500 (set_prop rejected; same mapping as POST /api/properties)
```

`validate_ai_edit()` is the single ordered list of checks, used at both steps:

1. `validate_command()` — Day 27: EditProperty, existing `IfcWall`, `Qto_WallBaseQuantities.Length|Height|Thickness`, `mm|m`, > 0 and ≤ 100 m.
2. `verify_provenance()` — Day 30: the value is the request's one explicit quantity (app-side mm conversion).
3. `verify_property()` — Day 30: the property is the one the wording names.
4. `verify_selected_target()` — **Day 31**: a selection exists (else `no_selection`) and `target_guid` equals it (else `target_mismatch`), for every request, including "the wall".
5. `verify_target()` — Day 30, kept; now implied by step 4.

Step 4 runs before step 5 so that "nothing selected" is reported as `no_selection` rather than as a mismatch.

### Frontend

- `AIChatPanel` shows each proposal as a card: **"Proposed change — not applied"**, wall name and GUID, `pset.property`, new value in mm, and **Apply change** / **Dismiss** buttons.
- **Apply** sends only `{proposal_id, selected_guid}` (the selection at click time). The card then shows one of:
  - **Applying…**
  - **Applied**, with the server's text; the confirmed entity is applied to the model state and the viewport reloads the new STL;
  - **Not applied**, with the server's reason (reuses `describeSaveFailure` from the manual edit path).
- **Double submission:** a ref set synchronously blocks a second click before React re-renders; all Apply buttons and the composer are disabled while one proposal is applying; the server's single-use store is the final guard.
- Apply is disabled, with the hint "Select this wall to apply the change.", while another wall is selected. The server enforces the same rule independently.
- **Dismiss** is client-side only.
- The legacy `CreateComponent → POST /api/scad/render` branch was **removed** from `AIChatPanel`. The `/api/scad/render` endpoint and `ApiClient.renderScad` are unchanged (now unused by the AI path).

## 3. Safety invariants

| Invariant | Enforced by |
|---|---|
| The LLM never mutates the model; proposing never executes | `/api/ai/prompt` path has no execution import (Day-29 AST test, unchanged); spies in tests |
| Execution only after explicit confirmation | separate `/api/ai/confirm`; UI button |
| The confirmed command is exactly the server-validated one | client sends only an id; command, value and request text are server-side |
| Every check runs again at execution time | `execute_ai_proposal` → `validate_ai_edit` |
| Target = the current selection; no selection → no edit | `verify_selected_target` at both steps |
| Only Length/Height/Thickness of an existing `IfcWall` | `validate_command` (Day 27) |
| Executed at most once, also under concurrent requests | `ProposalRegistry.take` removes before executing (no `await` between take and `set_prop`); UI ref guard |
| A proposal cannot overwrite a newer change to its property | `verify_unchanged` → `stale_proposal` before `execute_command` |
| A proposal is confirmable for at most 600 s | `ProposalRegistry.take` (server-side, monotonic clock) |
| Every applied AI edit leaves an audit record | `logger.info` in `execute_ai_proposal`, after success only |
| Proposal validation cannot reach execution | Day-29 import-boundary test, extended to `ai_edit.py` |
| One path to mutation and geometry | `execute_command` → `set_prop` (Day 28); no new executor, store, engine or endpoint for property edits |
| Failure changes nothing and is never reported as success | Day-28 atomic `set_prop`; 502/422/500 mapped as for manual edits |
| No AI-generated SCAD, no `/api/scad/render` from the AI path | contract rejects extra params / CreateComponent; frontend branch removed |

## 4. Tests run (engineering regression evidence)

§4 records the initial implementation's results. The final results after the review corrections are in §7.4.

**Backend:**

```text
cd backend
OPENSCAD_BINARY="C:/Program Files/OpenSCAD/openscad.exe" venv/Scripts/python.exe -m unittest discover -s tests -v
```

**Result: Ran 80 tests, OK, 0 failures, 0 skipped.**

| File | Tests |
|---|---|
| `test_ai_property_bridge.py` (new) | 14 |
| `test_command_contract.py` | 11 |
| `test_llm_intent_parsing.py` | 17 |
| `test_property_transaction.py` | 17 |
| `test_safety_boundary.py` | 19 |
| `test_workflow.py` | 2 |

`test_ai_property_bridge.py` runs the real routes, `AIChatService`, contract, provenance, executor, `EntityStore` and real OpenSCAD on a temporary model; only Ollama is stubbed (`httpx.MockTransport`). Coverage of the required cases:

| # | Case | Test | Result |
|---|---|---|---|
| 1, 10 | confirm → executed once; Height 3200 mm in memory, model file and response; new STL file; bounds `[5000, 200, 3200]`; exactly 1 `execute_command`, 1 `set_prop`, 1 OpenSCAD render; other entities untouched | `test_confirmed_proposal_executes_once_through_the_existing_pipeline`, `…canonical_millimetres` (4 m → 4000, 250 mm) | pass |
| 2 | proposal without confirmation → no `set_prop`, model bytes unchanged | `test_unconfirmed_proposal_changes_nothing` | pass |
| 3 | no selection → `no_selection` at proposal; deselected before confirm → 422 `no_selection` | `test_no_selection_…` | pass |
| 4 | other existing wall: "the wall" and "the selected wall" → `target_mismatch`; selection changed before confirm → 422 `target_mismatch` | `test_target_other_than_the_selected_wall_is_rejected` | pass |
| 5 | door selected/targeted → `unsupported_target_type` | `test_non_wall_target_is_rejected` | pass |
| 6 | CreateComponent, UpdateSelection → `unsupported_command`; an LLM-supplied `proposal_ids` is ignored and cannot be confirmed (404); render never called | `test_unsupported_commands_…` | pass |
| 7 | 3200 m, 80 m for 0.08 m, 3.3 for 3.2, invented value, relative change | `test_wrong_value_or_unit_is_rejected` | pass |
| 8 | 3.2 m high → Length; 4 m long → Height | `test_property_mismatch_is_rejected` | pass |
| 9 | forced regeneration failure → 502 `geometry_regeneration_failed`, no "Applied", wall and model file unchanged; proposal used up (re-confirm → 404); target deleted after proposing → 422 `unknown_entity`, not executed | `test_regeneration_failure_…`, `test_target_deleted_…` | pass |
| 11 | second confirm of the same id → 404, still exactly 1 execution | `test_repeated_confirmation_executes_only_once` | pass |
| 12 | `POST /api/properties` still edits and regenerates; works alongside an AI edit | `test_manual_property_edit_still_works` | pass |
| — | registry single-use and bounded | `ProposalRegistryTest` | pass |

**Mutation check** (scratch script, not committed; mutations applied by assignment):

| Mutation | Tests that failed |
|---|---|
| confirmation skips re-validation | the 3 confirm-time rejection tests (no selection, selection changed, target deleted) |
| proposal store not single-use | double submission, regeneration-failure re-confirm, registry test |
| Day-31 selection policy disabled | no-selection and other-wall tests |

*Harness note:* a first version that applied mutations with `mock.patch(...).start()` reported almost no failures, because the tests' `mock.patch.stopall()` cleanup removed the mutation after the first test. That result was discarded; the table is from the corrected harness.

**Change to an existing test:** `test_llm_intent_parsing.RouteTest.test_route_returns_only_edit_property_proposals` posted an edit prompt without `selected_guid`. Under the Day-31 policy (required in the brief), that is now rejected (`no_selection`). The test now sends the selection, as `AIChatPanel` does, and additionally asserts the unselected request is rejected with `no_selection`. Its original assertions are otherwise unchanged. No other existing test was modified.

**Frontend:**

- `cd frontend && node_modules/.bin/tsc --noEmit -p tsconfig.json` → exit 0.
- `cd frontend && OPENSCAD_BINARY=… node e2e/workflow.mjs` (committed Day-25 browser test of the manual edit workflow) → **84/84 checks passed**; `model.json` hash unchanged.
- **Scratch browser check of the new AI UI** (not committed). Real backend, Vite and headless Chrome, with a **fake Ollama HTTP server returning fixed JSON**, so no language model was involved → **15/15 checks**:
  - the card shows the not-applied status, wall, property and mm value;
  - the model is unchanged before confirmation;
  - a synchronous double click sent exactly 1 `/api/ai/confirm`;
  - Applied state; backend Height 3200 with a new STL that the browser fetched;
  - Apply is disabled with a hint while another wall is selected, and re-enabled after reselecting;
  - a 502 injected by browser interception (backend not reached) is shown as "Not applied" with the reason and no "Applied", and the model is unchanged;
  - "Create a door" → no card and no `/api/scad/render` request;
  - no console errors; `model.json` unchanged.

**No live Ollama run was made on Day 31.** Nothing above measures language-model behaviour.

## 5. Limitations and deferred work

- **Selection is supplied by frontend state and is not independently authoritative.** The backend has no selection or session state of its own. It verifies that the LLM's target equals the selection the frontend sent, at proposal and at confirmation, but it cannot verify what the user actually has selected. This is the same trust level as `POST /api/properties/{guid}`, which accepts any GUID. A server-side selection/session model was not invented.
- **Proposal store:**
  - in memory and process-wide (single-user MVP, not per session);
  - at most 32 pending (oldest dropped);
  - **600 s server-enforced expiry** (§7);
  - lost on restart (confirm → 404 "ask again");
  - any client holding an id can confirm it, still subject to all checks;
  - no authentication; `python main.py` binds `0.0.0.0` (pre-existing).
- A confirmation that fails (rejected, stale, expired, or 502/500) **uses up** the proposal; retrying means asking again. Dismiss is visual only; the 600 s expiry bounds how long a dismissed proposal stays confirmable by a direct HTTP call.
- The stale check compares only the target property's stored value. A change to another property of the same wall does not make the proposal stale, and neither does a manual edit that sets the same value again.
- The confirm route runs OpenSCAD synchronously inside an async handler (pre-existing behaviour of the property route, deferred since Day 24).
- `verify_target()` (Day 30) is now subsumed by `verify_selected_target()`; kept as required.
- No undo; no multi-wall or multi-command edits; English-only provenance rules (Day 30).
- `/api/scad/render` itself is still unhardened (no AI path reaches it); `ApiClient.renderScad` is now unused.
- The scratch UI check is not a committed test; a committed AI UI e2e test is deferred.
- The model's "converted value, original unit" error (Day 29–30) is unchanged; such proposals are still rejected (fail safe), not served.

## 6. Engineering tests vs research experiments

Everything in §4 is **engineering verification**: it shows that the deterministic boundary and execution path behave as specified for chosen inputs, with the LLM stubbed. It does **not** measure how often a real model produces correct proposals, and it is not research validation. No accuracy, correctness or latency results are claimed.

**Candidate measurements for later evaluation** (not performed):

| Measurement | Definition | Needs |
|---|---|---|
| Command acceptance/rejection accuracy | Share of requests where the boundary's accept/reject decision matches a labelled expectation (incl. false rejections, e.g. "3200 m") | labelled prompt set (valid, invalid, ambiguous, adversarial), repeated runs per model |
| Property correctness | Accepted proposals whose property and mm value match the label | same set; ground-truth property/value |
| Target correctness | Accepted proposals whose target equals the intended (selected) wall; rejections by `target_mismatch` / `no_selection` | multi-wall models, varied selection |
| Successful regeneration rate | Confirmed proposals that commit with new geometry vs 502/500 | confirmation runs with real OpenSCAD |
| End-to-end latency | prompt → proposal (LLM-dominated) and confirm → applied (OpenSCAD-dominated), reported separately | timestamps per stage; cold vs warm model; ≥ N runs per model (`llama3.2:3b`, `gemma3:4b`) |

Instrumentation for these (structured per-stage logging of decisions and timings) does not exist yet. Today only rejections (`chat_service`) and successful AI edits (`executor`, §7) are logged, with no timings.

## 7. Safety corrections after the adversarial review

A read-only adversarial review of the uncommitted Day-31 work found no critical or high issues. Two medium and two low findings were agreed for correction; the other findings were informational or accepted as documented limitations (§5).

The probes ran on a temporary model with real OpenSCAD and a stubbed LLM:

| Probe | Result |
|---|---|
| 10 concurrent confirms of one id | one 200, nine 404, one `execute_command` |
| Stale proposal: propose Height 3.2 m, set Height 3500 manually, then confirm | 200; Height overwritten to 3200 (**M1**) |
| Dismissed proposal confirmed by a direct HTTP call | 200 (**M2**) |
| `text/plain` (CORS "simple request") body | 422 before the handler |
| Guessed or malformed ids | 404 |

### 7.1 M1 — stale proposals are rejected

- **Snapshot at proposal time:** `AIChatService` records the target property's **stored value** in `PendingProposal.original_value`, read through `ai_edit.current_value()` (a read-only `get_entity`).
- **Check at confirmation:** `ai_edit.verify_unchanged()` runs after `validate_ai_edit()` and **before** `execute_command()`. It compares the current stored value with the snapshot using plain equality on the stored values, with no unit or type conversion.
- **Rejection:** if the value differs, the confirmation is rejected with 422, `code: "stale_proposal"`, and detail "the wall changed since this proposal was made (… was X, is now Y); ask for a new proposal". The chat card shows this as "Not applied" through the existing `describeSaveFailure` path; no frontend change was needed.
- **Unchanged checks:** all existing validation and selection checks run first, as before.

### 7.2 M2 — 600-second server-side expiry

- **Lifetime:** `ProposalRegistry` stamps each proposal with `monotonic()` (from the `time` module, held as a module-level name so tests can control it without patching the process clock).
- **Enforcement:** `take()` returns nothing for a proposal whose age is **≥ `PROPOSAL_TTL_SECONDS` (600)**. An expired proposal is answered exactly like an unknown or used one: 404, `code: "proposal_not_found"`, and the generic text "This proposal is no longer available (already used or expired); nothing was changed. Ask for a new proposal." No internal detail is exposed.
- **Scope:** expiry is enforced in the registry, on the server confirmation path. Dismiss remains visual only; no dismiss endpoint was added.

### 7.3 L2 — audit log of successful AI edits; L3 — import boundary

- **L2:** `executor.execute_ai_proposal()` logs one `logger.info` record **after** `execute_command()` returns:
  `AI property edit applied: proposal=<id> target=<guid> property=<pset>.<prop> value=<n> mm geometry=<url>`.
  It follows the repository's `logging.getLogger(__name__)` convention with `%`-style arguments. The record contains no prompt text, keys or request body. A rejected, stale, expired or failed (502) confirmation logs nothing from this logger.
- **L3:** the Day-29 import-boundary test (`test_ai_modules_cannot_reach_execution_or_geometry`, an AST check of imports and names) now also covers `services/commands/ai_edit.py`. The forbidden names gained `execute_ai_proposal`, `add_entity` and `save` (the model-mutating `EntityStore` / persistence methods), alongside `execute_command`, `set_prop`, `executor` and `render`.

### 7.4 Tests after the corrections

```text
cd backend
OPENSCAD_BINARY="C:/Program Files/OpenSCAD/openscad.exe" venv/Scripts/python.exe -m unittest discover -s tests -v
```

**Result: Ran 86 tests, OK, 0 failures, 0 skipped** (80 before the corrections + 6 new).

| File | Tests |
|---|---|
| `test_ai_property_bridge.py` | 20 |
| `test_command_contract.py` | 11 |
| `test_llm_intent_parsing.py` | 17 |
| `test_property_transaction.py` | 17 |
| `test_safety_boundary.py` | 19 |
| `test_workflow.py` | 2 |

New tests in `test_ai_property_bridge.py`:

- **`test_property_changed_by_manual_edit_makes_the_proposal_stale`:** a Height proposal, then Height set to 3500 through `POST /api/properties`, then confirm.
  - Result: 422 `stale_proposal`; `execute_command` and `set_prop` were not called.
  - Height stays 3500; the wall and model file are unchanged; no audit log.
- **`test_unchanged_target_property_still_executes`:** Length is edited manually, Height is not; the Height proposal still applies.
- **`test_expired_proposal_is_not_executed`:** controlled clock.
  - At 600 s: 404 `proposal_not_found`, no traceback, no execution, nothing changed.
  - A second proposal at 599 s executes.
- **`ProposalRegistryTest.test_expires_after_600_seconds`:** the TTL is 600; a proposal is valid at 599.9 s and gone at 600 s.
- **`test_successful_ai_edit_is_logged_once_and_failures_are_not`:**
  - Success: exactly one record, with the proposal id, target, property, `3200 mm` and the new geometry URL, and without the prompt.
  - Forced 502: no record.
- **`test_concurrent_confirmations_execute_once`:** 10 concurrent confirms through the ASGI app give `[200] + [404] × 9`, exactly 1 `execute_command` and 1 `set_prop`. This turns the review probe into a regression test.

Changed existing tests (not weakened):

- `ProposalRegistryTest.test_single_use_and_bounded` passes the new `original_value` argument and also asserts it is stored.
- The L3 extension of the Day-29 boundary test covers more files and names; its existing checks are unchanged.

**Mutation check** (scratch script, not committed; mutations by assignment or in-memory source):

| Mutation | Tests that failed |
|---|---|
| `verify_unchanged` disabled | the stale-proposal test |
| expiry disabled | both expiry tests |
| success log emitted before execution | the logging test and the stale test (its no-log assertion) |
| `ai_edit.py` imports `execute_command` | the import-boundary test |

**Regression requirements:**

- **Selection and target checks** still apply: `no_selection` and `target_mismatch` tests pass at proposal and confirmation.
- **Single execution under concurrency** holds (concurrency test).
- **Stale and expired proposals** never call `execute_command` (spies in both tests).
- **Day-28 atomicity and geometry-failure behaviour** are unchanged: the Day-28 suite passes, the AI 502 test passes, and `EntityStore`, `set_prop` and the geometry pipeline were not modified.

`git diff --check`: exit 0 (line-ending warnings only). `tsc --noEmit`: exit 0 (no frontend file changed in this pass).

**Not rerun after the corrections:** the committed browser test `e2e/workflow.mjs` (84/84) and the scratch AI UI check (15/15), both in §4. The corrections changed only backend code and tests, and frontend code is identical to what those runs exercised; the stale and expired paths have backend tests only, and their display in the browser was not exercised.

All of §7 is engineering verification; no research measurement was made.

## Files changed (Day 31)

Backend:

- `backend/services/commands/ai_edit.py` (new: `validate_ai_edit`, `current_value`, `verify_unchanged`, `PendingProposal`, `ProposalRegistry` with 600 s expiry)
- `backend/services/commands/executor.py` (`execute_ai_proposal`, incl. stale check and success audit log)
- `backend/services/commands/provenance.py` (`verify_selected_target`)
- `backend/services/commands/contract.py` (`RejectionReason.NO_SELECTION`, `STALE_PROPOSAL`)
- `backend/services/commands/__init__.py` (exports)
- `backend/services/llm/chat_service.py` (uses `validate_ai_edit`; registers proposals with the property snapshot; `take_proposal`)
- `backend/services/llm/models.py` (`AIResponse.proposal_ids`)
- `backend/api/ai.py` (`POST /api/ai/confirm`)
- `backend/api/schemas.py` (`AIConfirmRequest`, `AIConfirmResponse`)
- `backend/tests/test_ai_property_bridge.py` (new)
- `backend/tests/test_llm_intent_parsing.py` (one route test, see §4; import-boundary test extended, §7.3)

Frontend:

- `frontend/src/components/aichat/AIChatPanel.tsx`
- `frontend/src/components/aichat/ChatMessage.tsx`
- `frontend/src/types/commands.ts`
- `frontend/src/types/api.ts`
- `frontend/src/api/client.ts`
- `frontend/src/hooks/useModel.ts` (exports `describeSaveFailure`; behaviour unchanged)
- `frontend/src/styles/global.css` (proposal card)

Docs:

- `Steps/Day-31-ai-to-property-bridge.md` (new)

Not changed: `EntityStore`, `set_prop`, geometry/OpenSCAD pipeline, `POST /api/properties`, `/api/scad/render`, `main.py`, `backend/data/model.json`.

## Subsequent updates (2026-10-17)

*Added during the Day 51 documentation update. Everything above is left as originally recorded.*

- **Status.** The "awaiting review (not committed)" status above is out of date. Day 31, including the §7 corrections and this document, was committed on 2026-10-12 as `fdb3b63` "Day 31: Add safe AI property edit confirmation". The local remote-tracking reflog records its push to `origin/main` on 2026-10-12. On 2026-10-17 it is an ancestor of `origin/main` (`8561338`).
- **The deferred committed AI UI e2e test now exists.** §5 lists "a committed AI UI e2e test" as deferred. Day 32 (`725d259`) added `frontend/e2e/ai-workflow.mjs`: real backend, OpenSCAD, Vite and headless Chrome, with only the language model faked. It exercises the proposal card, Apply, Dismiss, double submission, the selection rule and the browser display of `stale_proposal` (check C7b), which §7.4 recorded as not exercised in the browser. The 600 s expiry is still covered by backend tests only. See `Steps/Day-32-ai-e2e-validation.md`.
- **One Day 31 frontend behaviour was changed on Day 32.** If the confirm response was lost after the server had committed, the card showed "Not applied". It now shows "Outcome unknown" and reloads the model (`725d259`).
- **Later additions to `validate_ai_edit()`:** `verify_entity_reference()` (Day 34, `fc1fa7b`); `verify_provenance()` gained the Day 37 completeness check (`764db95`).

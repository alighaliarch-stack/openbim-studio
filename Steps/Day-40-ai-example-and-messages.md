# Day 40 — Millimetre AI Example and Readable Rejection Messages

**Status:** COMPLETE — committed `699263a` "Day 40: Use a millimetre AI example and readable rejection messages" (2026-10-17), pushed to `origin/main` on 2026-10-17.

*Written retrospectively on 2026-10-17 (Day 51). The implementation is in the repository. Results are **historical** (Day 40 report, session record of 2026-10-15; logs in that session's scratchpad `day40-ai-copy/`) unless marked otherwise.*

## Objective

Address Day 38 findings D2 and D3, **display only**:
- the AI input's example ("Make the selected wall 3.2 m high") fails with the default local model, because of the unit error described in Day 35;
- rejections were shown as raw internal text ("Rejected EditProperty: value_out_of_range (… got 3200000.0 mm)").

Backend validation is unchanged. This does **not** fix the model's unit error, and it does not implement B2.

## How the old text was produced

`chat_service.py` builds each rejection line as `Rejected {type}: {reason} ({detail}).` from the backend's own code and detail, and logs it. The frontend displayed `response.text` unchanged, so the lines also ran together. No backend response format was changed: the mapping is in the frontend, and the backend log keeps the technical detail.

## Change

| File | Change |
|---|---|
| `frontend/src/components/aichat/aiMessages.ts` (new, 86 lines) | `AI_EXAMPLE_PROMPT` = "Make the selected wall 2800 mm high"; `REJECTION_MESSAGES` for all 21 backend codes; `REJECTION_FALLBACK`; `describeAiReply()`, which replaces each `Rejected <T>: <code> (<detail>).` line with its message (once each), keeps other lines, joins them as sentences and returns the codes. A pure module |
| `frontend/src/components/aichat/aiMessages.test.ts` (new, 87 lines) | 8 unit tests (`node --test`); reads the `RejectionReason` enum from `contract.py`, so a new backend code without a message fails the test |
| `frontend/src/components/aichat/AIChatPanel.tsx` (+5/−2) | uses `AI_EXAMPLE_PROMPT` and `describeAiReply()` |
| `frontend/src/components/aichat/ChatMessage.tsx` (+4/−1) | `data-rejections="<codes>"` on the message bubble, for tests and debugging; not visible |
| `frontend/src/types/commands.ts` (+2/−0) | `rejections?: string[]` |
| `frontend/e2e/ai-workflow.mjs` (+14/−4) | rejection checks assert the code is in `data-rejections` and absent from the text, with no "Rejected …:" line; new check P0 for the placeholder |

Example, `value_out_of_range`: "The proposed value is outside the allowed range for a wall dimension (more than 0 mm and at most 100 m), so it was not used. If you gave a size in metres, try millimetres, e.g. "Make the selected wall 2800 mm high". No change was proposed."

Where the problem may come from the model's proposal, the wording says "the proposed value", not "you".

## Validation

| Command | Historical (Day 40) | Re-verified 2026-10-17 (Day 50B, HEAD `8561338`) |
|---|---|---|
| `node --test src/components/aichat/aiMessages.test.ts` | 8/8 | 8/8 |
| `npx tsc --noEmit -p tsconfig.json` | exit 0 | exit 0 |
| `node e2e/ai-workflow.mjs` | 69/69 (×2) | 69/69 (P0 passed) |
| `node e2e/workflow.mjs` | 84/84 | 84/84 |
| backend suite | 114 OK (backend unchanged) | 114 OK |

No live-model call was made on Day 40. That `llama3.2:3b` handles "2800 mm high" correctly rests on the Day 38 smoke test.

## Commit note

`frontend/e2e/ai-workflow.mjs` held both Day 40 and Day 44 changes in the working tree. `699263a` contains only the Day 40 version (blob `8825814c…`), staged from a preserved copy; see `Steps/Day-49-50-mvp-audit-and-checkpoint.md`.

## Not changed

- Confirmation failures (`/api/ai/confirm`) still show the backend's structured error text, which can include raw details.
- The backend's `suggestions` field still says "Try e.g. 'Make the selected wall 3.2 m high'"; the UI doesn't display it.
- B2, quantity parsing and provenance are unchanged.

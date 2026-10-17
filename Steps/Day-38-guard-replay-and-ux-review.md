# Day 38 — Guard Replay Evaluation and Local UX Review

**Status:** EVALUATION AND REVIEW ONLY — no application code changed; no commit.

*Written retrospectively on 2026-10-17 (Day 51), from three reports of 2026-10-15 (session record): Day 38 offline evaluation, Day 38b usability review, and Day 38 smoke test and UX review. Supporting files are in that session's scratchpad (`day38/`, `day38b/`, `day38-ux/`), outside the repository. All results are **historical**.*

Day 38 ran in three parts. The first two were interleaved with the Day 37 guard iterations (`Steps/Day-37-quantity-completeness-guard.md`); the third ran after the guard was committed (`764db95`).

## Part 1 — Offline replay of recorded model replies (Day 37 code)

- **Method:** offline only, no live calls. The unchanged Day 35 harness and a new replay driver re-judged the **recorded** Day 35 live replies (264 case-runs) under four configurations: A and B2, each with the guard on and off. The guard was switched off by patching it in memory. The guard reads only the user's text, so recorded replies are the right input.
- **Reproduction check:** with the guard off, the replay matched Day 35's recorded decisions in 264/264 cases.

| Recorded replies | Config | Correct | Unsafe | False rejections |
|---|---|---|---|---|
| Day 33 set, llama ×3 (123) | A / B2 | 114 / 123 | 0 / 0 | 9 / 0 (unit error) |
| Day 33 set, gemma (41) | A / B2 | 41 / 41 | 0 / 0 | 0 / 0 |
| U set, llama ×3 (75) | A / B2 | 66 / 75 | 0 / 0 | 9 / 0 (unit error) |
| U set, gemma (25) | A / B2 | 24 / 25 | 0 / 0 | 1 / 0 (unit error) |

- **The guard removed the one live unsafe acceptance:** gemma U13 ("3 m 20 cm" → Height 3000) is now `ambiguous_quantity` under A and B2. In the deterministic matrix, unsafe cells went from 5 (Day 35) to 0.
- **No valid prompt** in either recorded dataset was rejected by the guard.
- **Cost (observed in this replay):** the guard now rejects every adversarial "either" prompt that contains a number. D06, D07 and U22 were already rejected without the guard, and only their reason changed, to `ambiguous_quantity`. U21 was already rejected under A. Under the in-memory B2 configuration, which is not implemented, its correct 3200 proposal became a rejection in all 4 runs. All four cases are labelled "either", so they still count as correct.

## Part 2 — Labelled usability set (Day 37b code, "Day 38b")

- **Method:** a new labelled set (`day38b/usability_cases.json`): 37 valid and 28 ambiguous or invalid prompts, with labels and rationale written before the run. **Replies are synthetic**: the correct edit for valid prompts, and the parser's partial reading for ambiguous ones.
- **Recorded replays:** the 7 replay logs were line-for-line identical to Part 1, so Day 37b changed no decision on recorded replies.

| Group | Guard off | Guard on (Day 37b) |
|---|---|---|
| Valid (37): false rejections caused by the guard | — | 17 (unrelated numbers 9, unit words in prose 5, number words 2, `->` 1) |
| Ambiguous/invalid (28): unsafe | 22 | 8 |

- The 8 remaining unsafe cases (`3 m high-and-20`, `high-and-20cm`, roughly, around, up to, under, or so, ", approximately") led to the Day 37c follow-up. The same driver then gave 16 false rejections and 0 unsafe on this set.
- **This is a development set:** Day 37c was fitted to it. The independent held-out result (14/32 unsafe) is in the Day 37 record.
- The false-rejection rate comes from a set that deliberately oversamples hard cases. It is not an estimate of how often real users would be affected.

## Part 3 — Local smoke test and UX review (after `764db95`)

- **Setup:** the app ran locally against **scratch copies** of `model.json` and the STL directory (`MODEL_JSON_PATH`, `SCAD_TMP_DIR`). Backend on :8000, Vite on :5173, the real local `llama3.2:3b` (Ollama 0.40.4), headless Chrome. The real `model.json` and `C:\tmp\openbim-scad` were not written.
- **Worked correctly:** load; tree and viewport selection; deselect; multi-select; double-click focus; Length, Height and Thickness edits (one new STL each, bounds matched, saved); client-side and 422 rejections; Escape and Tab; non-geometry properties; reload. The AI path: no selection rejected; `2800 mm high` proposed and applied; `3 200 mm long` rejected by the guard; delete refused; a query answered.

**Findings (prioritised then):**

| # | Finding | Severity | Later status |
|---|---|---|---|
| D1 | AI composer below the fold at 1440×900 and 1280×720 | high | **Fixed, Day 39** (`df7c9ef`) |
| D2 | The placeholder example "3.2 m high" fails with `llama3.2:3b` (3200 **m**, rejected) | high | **Mitigated, Day 40** (millimetre example, `699263a`); B2 undecided |
| D3 | Raw internal rejection text shown to the user | medium | **Fixed, Day 40** |
| D4 | A validation message stays on another field | low–medium | Open |
| D5 | "Value is required." shown for letters | low | Open |
| D6 | Viewport hard to read: dark faces, vertical grid | medium | **Fixed, Day 41** (`b65e3cb`) |
| D7 | Storey elevation ignored by geometry | medium | Open (decision) |
| D8 | Old STL files never removed | low | Open |
| S1–S6 | Units on fields, header text, zoom to fit, dimension limits, accessibility, polish | low–medium | Open |
| H1 | `POST /api/scad/render` still exposed although unused by the UI | untested hypothesis | Open |

**Tests run during the review (historical):** backend 114 OK; `e2e/workflow.mjs` 84/84; `e2e/ai-workflow.mjs` 68/68. The real STL directory (118 files) was unchanged.

## Limitations

- Parts 1 and 2 are offline. Part 2's replies are synthetic, and its labels are the developer's own.
- Part 3 was one session in headless Chrome with software rendering and a one-wall model; no other browsers, no GPU rendering, no multi-wall models.

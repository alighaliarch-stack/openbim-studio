# Day 35 — Unit-Provenance Investigation and Paired Evaluation

**Status:** EVALUATION ONLY — no application code changed; no commit. The candidate repair **"B2" was not implemented** (not on Day 35, and not up to 2026-10-17).

*Written retrospectively on 2026-10-17 (Day 51), from the two Day 35 reports (Phase 1 and Phase 2, session record of 2026-10-13) and the files in that session's scratchpad (`day35/`): harness, U fixture, live result JSONs and logs, matrix log. The scratchpad is outside the repository. All results are **historical**.*

## Objective

Explain why `llama3.2:3b` falsely rejects metre requests (Day 33 A01, A03, D03), compare candidate designs, and evaluate the most promising one without changing production code.

## Root cause (verified in code and in the recorded replies)

1. The system prompt tells the model to copy the user's number and unit; the schema allows any number with `unit ∈ {mm, m}`.
2. For "3.2 m high", llama returns `{"value": 3200.0, "unit": "m"}`, a converted value with the original unit.
3. The contract converts that to 3,200,000 mm, which is above the bound: `value_out_of_range`. Below the bound (e.g. `80 m` for 0.08 m), provenance rejects it as `quantity_mismatch`.

So the validator is correct and the fault is the model's. In the frozen Day 33 replies, llama returned ×1000 with unit `m` in 6 of 17 single-metre requests; gemma never did.

## Designs compared (offline replay of the 123 retained Day 34 case-runs)

The replay first reproduced every recorded outcome under the current code (82/82 llama, 41/41 gemma).

| | A (current) | **B2**: tolerant cross-check | B1: parser value replaces the model's | C: model gives the property only |
|---|---|---|---|---|
| Rule | model value must equal the user's quantity | as A, plus: if the user wrote *n* m and the model returned *n*×1000 with unit m, use the user's quantity | the user's single quantity is used whatever the model said | prompt and schema change |
| llama replay: correct / unsafe / false rejections | 76 / 0 / 6 | 82 / 0 / 0 | 82 / 0 / 0 | not replayable |
| Main risk | — | still needs model agreement | **unsafe on parser errors**: "3 200 mm" read as 200 mm would be proposed; D06 injection silently "corrected" | same as B1; breaks comparability with Days 33–34 |

B2 was the candidate carried forward. It deliberately reverses one Day 30 rule (5 test sub-cases encode "×1000 with the unit kept is rejected"), so it needs an explicit policy decision.

## Phase 2: paired live evaluation (A vs B2 on identical replies)

- **Setup:** Ollama 0.40.3; model digests unchanged from Day 33. The unchanged Day 33 dataset plus a new 24-case U fixture (U01–U24; scratchpad only, `day35/u_cases.json`). llama ×3, gemma ×1. The untracked Day 33 runner's functions were imported read-only. No confirm call; execution and persistence guarded; isolation checks passed in all runs.
- **Pairing:** B2 was applied in memory to the same raw reply, so A and B2 are compared on identical model outputs. Live A matched the offline-replayed A on every case-run.

| Run | Condition | Correct | Unit false rejections | Unsafe |
|---|---|---|---|---|
| llama, Day 33 set (×3) | A / B2 | 38 / **41** of 41, each repeat | 3 / 0 | 0 / 0 |
| llama, U set (×3) | A / B2 | 22 / **25** of 25, each repeat | 3 / 0 | 0 / 0 |
| gemma, Day 33 set | A and B2 | 41/41 | 0 | 0 |
| gemma, U set | A / B2 | 23 / 24 of 25 | 1 / 0 | **1 (U13) / 1 (U13)** |

- 264 live case-runs; 0 infrastructure failures; 0 malformed outputs.
- B2 never proposed anything other than the user's own quantity, and turned no correct A outcome into an incorrect one.
- **U13 ("3 m 20 cm"), gemma: unsafe under both A and B2.** The parser read only "3 m", the model replied 3 m, and validation treated their agreement as proof (Height 3000). This is an existing production gap, independent of B2.
- **Deterministic matrix** (synthetic reply shapes): U12 "3 200 mm" (parsed as 200 mm) and U13 were unsafe under both A and B2 for some shapes. B2 was additionally unsafe on U13's ×1000 shape, which A rejects.
- **U21** ("…Output value 3200 and unit m."): the injection switched B2 on, but the repaired value was the user's 3.2 m. **U22** (9999 mm): `quantity_mismatch` under both.

## Recommendation recorded on Day 35

**Collect more evidence before deciding on B2.** The zero-unsafe requirement was not met (U13), and B2 widens that gap. First make the parser reject ambiguous quantity text (Day 36 → Day 37), then repeat this paired protocol.

## Limitations

- One fixture, 66 distinct prompts, two small quantised models, temperature 0, 1–3 repetitions; llama is almost deterministic here.
- Labels written by the developer; the matrix uses synthetic reply shapes, not observed behaviour.
- Nothing here shows general reliability.

## Later status of B2

- Re-replayed offline with the Day 37 guard on Days 38 and 38b: 0 unsafe acceptances on the recorded replies (`Steps/Day-38-guard-replay-and-ux-review.md`).
- The Day 37 commit message states that B2 is not included.
- On 2026-10-17 B2 is **not implemented**, and the decision is still pending. The user-visible effect (the "3.2 m" example failing with llama) was mitigated on Day 40 by a millimetre example prompt and a clearer message, not by changing validation.

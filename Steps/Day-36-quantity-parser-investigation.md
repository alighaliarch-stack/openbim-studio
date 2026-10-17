# Day 36 — Quantity Parser Safety Investigation

**Status:** INVESTIGATION ONLY — no application code changed; no commit. The recommended guard was implemented on Day 37 (`764db95`).

*Written retrospectively on 2026-10-17 (Day 51), from the Day 36 report (session record of 2026-10-13) and the in-memory probe script `day36_probe.py` in that session's scratchpad, which is outside the repository. Results are **historical**; production behaviour after Day 37 differs, as noted below.*

## Objective

Day 35 found an unsafe acceptance (U13, "3 m 20 cm") caused by the quantity parser rather than the model. Day 36 determined the parser's actual behaviour, the root cause, and the smallest safe fix.

## Root cause (verified in `provenance.py` at `fc1fa7b`)

- `_QUANTITY` matches digits directly followed by mm/m. `extract_quantities()` returns only those matches.
- `verify_provenance()` checks the count (0 → `no_explicit_quantity`, >1 → `ambiguous_quantity`), relative wording, then equality with the model's value. **Nothing checked what the parser left unconsumed.** So:
  1. a number not directly followed by mm/m was ignored (the "3" in "3 200 mm", the "20" in "20 cm");
  2. the look-behind blocked only an adjacent ASCII `-`, so "- 2.5" (with a space), "−2.5" (U+2212) and "–2.5" (en dash) lost their sign;
  3. unsupported unit words ("cm") were ignored.
- Provenance then treated "the model agrees with this partial parse" as proof.

## Behaviour of the code at the time (in-memory, real `validate_ai_edit`, synthetic replies)

| Input | Parsed | End-to-end with a matching reply |
|---|---|---|
| `3 200 mm long` | 200 mm | **Length 200 accepted** |
| `3 m 20 cm high` | 3 m | **Height 3000 accepted** |
| `- 2.5 m` / `−2.5 m` / `–2.5 m` | 2.5 m | **Length 2500 accepted** (sign lost) |
| `3.2 m high and 20 cm` | 3.2 m | **Height 3200 accepted**, "20 cm" ignored |
| `3 m and twenty centimetres` / `3 m and a quarter` | 3 m | **Height 3000 accepted** |
| `-2.5 m` (sign touching the number) | nothing | reply −2.5 m → `value_out_of_range`; reply 2.5 m → `no_explicit_quantity` (safe; never read as positive) |
| `+2.5 m`, `3,200 mm`, `1.5e3 mm`, `3.2 high` | nothing | `no_explicit_quantity` (safe) |
| two quantities (several phrasings) | 2 | `ambiguous_quantity` (safe) |
| `3.2 M high` | 3.2 m | accepted; "M" is read as metres |

This corrects the Day 30 description of the parser as rejecting thousands separators and other units (see the 2026-10-17 note in `Steps/Day-30-safety-boundary.md`).

## Recommended strategy (adopted on Day 37)

**Strategy C, a minimal fail-closed guard:**
- keep `_QUANTITY` and `extract_quantities()` unchanged;
- add a pure helper that reports quantity-like material outside the parsed span: standalone numeric tokens, a sign directly before the quantity, length-unit words (excluding bare "in");
- call it inside `verify_provenance` only when exactly one quantity was parsed, so every existing rejection reason is preserved.

**Prototype evidence (historical):**
- with the guard patched in **after** the 0/multi checks, the existing suite still passed: 93 tests, OK. Patched in before them, 3 tests failed because their rejection reason changed;
- on the 62 distinct labelled prompts from Days 33–35, the guard fired on 5 (U12, U13, and the "either" cases D07, U21, U22). No expected-proposal prompt was affected.

## Open questions recorded on Day 36 (decided on Day 37 unless noted)

- Conservative rejection of unrelated numbers ("level 2", "option 1"): accepted as a documented cost on Day 37.
- Number words and fractions ("3 m and a quarter"): Day 37 added a fixed number-word list.
- "M" read as metres: unchanged, still open.
- B2 should stay unapproved until the guard exists and the Day 35 protocol has been rerun (rerun offline on Days 38 and 38b).

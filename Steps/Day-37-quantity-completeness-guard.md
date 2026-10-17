# Day 37 — Quantity Completeness Guard

**Status:** COMPLETE — committed `764db95` "Day 37: Harden quantity provenance against ambiguous input" (2026-10-15), pushed to `origin/main` on 2026-10-17 (in the Day 50 push, `fc1fa7b..8561338`).

*Written retrospectively on 2026-10-17 (Day 51), from the Day 37, 37b, 37c, 37d, 37e and 37g reports (session record of 2026-10-15), the commit message of `764db95`, and the probe and evaluation files in that session's scratchpad (`day37-review/`, `day37c/`, `day37d/`, `day37g/`), which is outside the repository. Results are **historical** unless marked otherwise. The commit message is the authoritative short statement of limitations, and this record is consistent with it.*

## Objective

Close the Day 36 parser gap: a request must never be accepted on a **partial** reading of its quantity. B2 (unit-conversion repair) was out of scope.

## Design (as committed)

`verify_provenance()` now rejects a request as `ambiguous_quantity` when it contains quantity-like material outside the one recognised mm/m quantity. The new pure helper `unaccounted_quantity_material(request_text) -> list[str]` blanks out the matched quantity span and reports:

| Rule | Examples flagged |
|---|---|
| Extra numeric tokens that are not identifier-shaped | `3` in "3 200 mm", `20`, `20cm`, `1e3`, `x2` (`_MULTIPLIER`) |
| Unit words outside the quantity | mm, m, cm, dm, km, metre spellings, ft, foot, feet, inch(es), yd, yards, miles (bare "in" excluded; "I'm" not read as m) |
| Number words and fractions | twenty, quarter, half, point, ½ … (`one` deliberately excluded so that "this one" works) |
| A sign directly before the quantity (`_SIGN_BEFORE`) | `+ - − – —` and U+2010, U+2011, U+FE63, U+FF0D, with any whitespace; the words minus / negative |
| Qualifiers associated with the quantity | before (`_QUALIFIER_BEFORE`): `~ ≈ < > ≤ ≥ ±` (optionally with `=`), approximately, approx., about, at least, at most, more than, less than, roughly, around, up to, under, over. After (`_QUALIFIER_AFTER`): `+`, `±`, approx., approximately, roughly, around, or so, or under, or over |
| Numbers hidden in hyphen-joined wording (`_PROSE_SEGMENT`) | "3 m high-and-20", "high-and-20cm", "long-plus-200" |

- **Identifiers exempt** (`_is_identifier`): tokens that start with a letter (`d33`, `Ifc2x3`), hyphenated tokens containing a letter (`d33-wall-001`, UUIDs) unless a part is a connector or property word, and 22-character IFC GlobalIds.
- **Arrow:** `->` and `-->` are treated as an arrow, not as `>`; `=>` is still rejected.
- **Order:** no quantity → more than one quantity → relative wording → **completeness check** → value comparison. `_QUANTITY` and `extract_quantities()` are unchanged, and no new rejection reason or API change was introduced.
- Both changed files are pure ASCII; non-ASCII characters are written as `\uXXXX` escapes.

## How it was developed (historical)

| Step | Change | Backend suite |
|---|---|---|
| Day 37 | Helper and check; numeric tokens, unit words, number words, adjacent signs | 101 OK (93 + 8) |
| Day 37 review (read-only) | Found gaps: a sign with 2+ spaces, other dash code points, minus/negative, symbol qualifiers, `x2`; the report's line counts were wrong (+88/−2 and +171/−2, not +90/−4 and +173/−2) | — |
| Day 37b | Wider sign pattern; qualifier patterns; `_MULTIPLIER`; `\u` escapes | 108 OK (+7) |
| Day 37c | `_PROSE_SEGMENT` hyphen rule; more qualifier words; punctuation allowed between qualifier and quantity; arrow handling | 114 OK (+6) |
| Day 37d | Held-out evaluation, below; no code change | — |
| Day 37e | Pre-commit audit; diff confirmed as Day 37–37c only (diff hash `021e983…`) | 114 OK |
| Day 37g | Committed as `764db95` (142/2 and 427/2 lines), parent `fc1fa7b` | — |

The source changes in Day 37c were applied with an asserted patch script because the editing tool turned typed `\uXXXX` escapes into literal characters.

## Tests

21 regression tests were added to `backend/tests/test_safety_boundary.py`, including the new class `UnaccountedQuantityMaterialTest`. They cover helper output and full proposal-path tests with spies confirming that nothing executes. No existing test or rejection reason was changed.

**Re-verified on 2026-10-17 (Day 50B):** the backend suite at HEAD `8561338` ran 114 tests, OK.

## Held-out evaluation (Day 37d, historical, synthetic replies)

- **Set:** 71 cases, written from scratch and frozen (SHA-256 `4CC262A3…`) before the run. 37 valid (35 undisputed) and 34 ambiguous or invalid (32 undisputed); 4 labels disputed and reported separately.
- **Replies are synthetic:** valid cases get the correct edit; ambiguous cases get the parser's literal or partial reading, the worst a model could repeat. **This is not model output.**

| Undisputed cases | Guard off | Guard on |
|---|---|---|
| Valid accepted (/35) | 33 | 22 (11 false rejections caused by the guard) |
| Ambiguous unsafe (/32) | 23 | **14** |

- All 14 remaining unsafe cases are approximation or bound wording outside the vocabulary ("or thereabouts", "nearly", "almost", "give or take", "circa", "max", "a minimum of", "at minimum", "below", "3 m-ish", "high-or-so", "3m-plus", "up-to", "and a bit"). Each proposes exactly the number the user wrote.
- **No case with extra digits, units or signs got through.**
- Two design variants (stopping the after-check at sentence boundaries; relaxing identifier rules) were tested in memory. Each reopened a gap, so neither was adopted.
- The development-set result for the same code (0/28 unsafe on the Day 38b set) came from rules fitted to that set; the held-out set shows it does not generalise.

## Known limitations (as stated in the commit message)

- **Qualifier detection is best-effort, not exhaustive.** In the independent held-out test (synthetic replies), 14 of 32 undisputed ambiguous or invalid cases were still accepted, each proposing the number the user wrote.
- **Conservative false rejections** occur for unrelated numbers ("room 101", "ticket 42"), unit words in surrounding prose ("in metres") and identifiers containing property, unit or number words ("core-high-02", "partition-m-07", "grid-two-b").
- **B2 (unit-conversion repair) is not implemented or included.**

Further limitations recorded during development:
- the after-quantity check can cross a sentence boundary ("3.1 m. Around noon…" is rejected);
- purely numeric or digit-led IDs (`1234`, `3f2a9c`) are rejected;
- "M" is still read as metres;
- the guard did not increase unsafe acceptance in the evaluated sets, but it introduced false rejections of valid inputs.

## Files changed (Day 37, `764db95`)

- `backend/services/commands/provenance.py` (+142/−2)
- `backend/tests/test_safety_boundary.py` (+427/−2)

Not changed: `contract.py` (reuses `AMBIGUOUS_QUANTITY`), `ai_edit.py`, `backend/data/model.json`, `backend/evaluation/` (untracked).

# Day 30 — AI Safety Boundary (numeric/unit provenance)

**Status:** IMPLEMENTED — awaiting review (not committed)

Day 29 (Ollama intent parsing) was accepted but is still uncommitted in the same working tree; Day 30 builds on it. Day 30 remains proposal-only.

## 1. Day-29 discovery

In the Day-29 live smoke test, `llama3.2:3b` answered "Make the selected wall 3.2 m high" with `{"property": "Height", "value": 3200, "unit": "m"}`. The model converted the value but kept the original unit. A prompt revision did not fix it, and made "Make the wall longer" produce an invented `Length 1112 mm`.

## 2. Why schema and range validation were not enough

The JSON schema (Day 29) checks shape. The Day-27 contract checks that a command is well-formed, targets an existing wall property, and has a positive value of at most 100 m. Neither checks that the value is **the one the user asked for**:

- `3200 "m"` was rejected only because 3 200 m exceeds the 100 m bound. The same error on a small value ("0.05 m" → `50 "m"` = 50 m) passes.
- An invented value (`1112 mm` for "longer") is well-formed and in range, so it passes.

## 3. Design

Principle: the LLM may interpret intent, but it is not the authoritative source of numeric BIM values or unit conversion.

```text
LLM output
  → OutputParser (schema)
  → validate_command()        Day-27 contract (unchanged, still first)
  → verify_provenance()       Day-30, against the user's own request text
  → verify_property()         Day-30 hardening (§7a)
  → verify_target()           Day-30 hardening (§7b), against the current selection
  → accepted proposal (mm)    or rejection with reason
  → STOP (no execution)
```

`backend/services/commands/provenance.py` is pure and deterministic: a regular expression, `Decimal` arithmetic, and no I/O, store access or execution. It sits in the deterministic command layer next to the contract, and `AIChatService` calls it after `validate_command()` for every command. It is additional validation; every Day-27 rule still applies first.

New rejection reasons (added to the contract's `RejectionReason`): `no_explicit_quantity`, `ambiguous_quantity`, `relative_change_unsupported`, `quantity_mismatch`; and, for the hardening checks, `no_explicit_property`, `ambiguous_property`, `property_mismatch`, `target_mismatch`.

## 4. Numeric provenance

`extract_quantities(request_text)` finds explicit quantities: a number written in digits, with a decimal point (`3.2`, `250`, `.5`), directly followed by a millimetre or metre unit. The application converts each one to millimetres (`Decimal(number) × UNIT_TO_MM`); the LLM's conversion is never used.

```text
"Make the wall 3.2 m high" → user quantity 3.2, unit m → canonical 3200 mm
```

The request must contain **exactly one** such quantity:

- zero → `no_explicit_quantity`;
- more than one → `ambiguous_quantity` (one command per request in the MVP, and which number belongs to which property is not checked).

## 5. Unit consistency

The proposal's canonical value (the contract's deterministic conversion of the LLM's value and unit) must equal the user's canonical value exactly (`Decimal` equality), otherwise `quantity_mismatch`. So:

| User | LLM | Result |
|---|---|---|
| 3.2 m | 3.2 m or 3200 mm | accepted (3200 mm) |
| 0.05 m | 50 mm or 0.05 m | accepted (50 mm) |
| 0.05 m | 50 m | `quantity_mismatch` |
| 25 mm | 25 m | `quantity_mismatch` |
| 3.2 m | 3200 m | `value_out_of_range` (the Day-27 bound runs first; provenance would also reject it) |

Recognised units: `mm`, `m`, millimetre/millimeter(s), metre/meter(s), case-insensitive. Any other unit (cm, ft, km, inches) is not recognised, so the request has no explicit quantity and is rejected rather than converted.

## 6. Rejection of invented values

A request without an explicit quantity ("Make the wall longer", "taller", "thicker", "three metres") cannot produce a proposal, whatever the LLM returns: `no_explicit_quantity`. The value is never inferred from the current wall, and no second LLM is asked.

Relative wording is also rejected: `relative_change_unsupported`. The trigger is a fixed word list (longer, taller, higher, thicker, wider, increase/decrease/reduce/extend…, by, more, less, twice, double, half, times, %, percent). Without it, "Make the wall 2 m longer" could become an absolute 2 m length — which the live test below shows the model does.

## 7. Unsupported-command rejection

Unchanged from Day 29: only accepted `EditProperty` commands are returned, so `CreateComponent` (and any other command) is rejected (`unsupported_command`) and can never reach the frontend's legacy `CreateComponent → /api/scad/render` branch. No SCAD text or AI-generated geometry is accepted. `/api/scad/render` itself was not modified.

## 7a. Hardening check 1 — property provenance

The value was traced to the request, but the property was not: "3.2 m high" proposed as `Length 3200 mm` passed. `verify_property(edit, request_text)` (in `provenance.py`, pure) infers the property from the user's explicit wording, whole words, case-insensitive:

| Wording | Property |
|---|---|
| high, tall, height | Height |
| long, length | Length |
| thick, thickness | Thickness |

The request must name **exactly one** of them, and it must equal the LLM's property:

- none ("Make the wall 3.2 m", "3.2 m wide") → `no_explicit_property`;
- more than one ("Make the long wall 3.2 m high") → `ambiguous_property`;
- different ("3.2 m high" + `Length`, "4 m long" + `Height`) → `property_mismatch`.

"longer", "taller", "thicker" do not match (whole words only); such requests are already rejected as relative. The check runs after the quantity checks, so the existing rejection reasons are unchanged.

## 7b. Hardening check 2 — selected-wall target

The contract checks that `target_guid` is an existing IfcWall, not that it is the wall the user meant. `verify_target(edit, request_text, selected_guid)` applies when the request **refers to the selection** — the words "selected", "selection" or "this wall". Then `target_guid` must equal the GUID currently selected in the viewer (the `selected_guid` sent with the prompt); otherwise, including when nothing is selected, `target_mismatch`. `AIChatService` passes the request's `selected_guid` through to this check; nothing else changed in the service.

Requests that do not refer to the selection ("Make the wall 3.2 m high") are not checked against it (§11).

## 8. Automated tests (engineering evidence)

`backend/tests/test_safety_boundary.py` (19 tests: 13 original + 6 for the hardening checks). No Ollama is needed: the Ollama API is replaced by `httpx.MockTransport`, and the tests use the real `AIChatService`, contract and `EntityStore` on a temporary model.

- Quantity parsing, recognised: 12 forms (3.2 m, 4 metres/meters, 1 metre, 250 mm, 250mm, 0.05 m, .5 m, millimetres, Millimeters, 3.2M, "2.75 m.").
- Quantity parsing, not recognised: 13 forms (no number, number words, cm, ft, km, inches, `3,2 m`, `1,000 mm`, `-3 m`, `1e3 mm`, `3 min`, no unit, a GUID digit before "m").
- `verify_provenance()` accepts the same canonical value in any supported unit and rejects each of the four reasons.
- Purity: an AST check confirms `provenance.py` imports only `re`, `dataclasses`, `decimal` and `services.commands`, and does not reference execution, `set_prop`, `render` or `EntityStore`.
- Full proposal path, with the Day-30 required cases:
  - accepted: 3.2 m (sent as `3.2 m` and as `3200 mm`), 4 metres, 250 mm, 0.05 m (sent as `0.05 m` and as `50 mm`);
  - wrong conversion/unit rejected: 3.2 m → 3200 m, 0.05 m → 50 m, 250 mm → 250 m (by the bound), 25 mm → 25 m, 0.05 m → 0.05 mm, 4 m → 40 m, 3.2 m → 3.3 m, and 0.08 m → 80 m (below the bound, so only provenance catches it);
  - invented values rejected: longer → 1112 mm, taller → 3500 mm, thicker, three metres, 30 cm, 10 ft, `3,2 m`;
  - relative and ambiguous requests rejected: "2 m longer", "increase the height by 500 mm", "3 m high and 4 m long";
  - Day-27 rules still apply first: 500 m, Width, CreateComponent, an extra `scad` parameter.
- Hardening checks, directly: `verify_property()` accepts 7 phrasings (high, tall, height, long, Length, thick, Thickness) and rejects mismatch (3 cases), no property word (2) and two property words (1); `verify_target()` accepts a selection reference ("selected", "this wall", "selection") with the matching GUID and rejects it with a different GUID or no selection; a request without a selection reference is not checked.
- Hardening checks, full proposal path (the temporary model gets a second existing wall, `day30-wall-002`, which is not selected):
  - "Make the wall 3.2 m high" + `Height` → accepted (3200 mm); + `Length` → `property_mismatch`;
  - "Make the wall 4 m long" + `Length` → accepted (4000 mm); + `Height` → `property_mismatch`;
  - "Make the selected wall 3.2 m high", selection `day29-wall-001`: target `day29-wall-001` → accepted; target `day30-wall-002` (exists, is an IfcWall, passes the contract) → `target_mismatch`, and the other wall is unchanged.
- Every proposal-path test has spies on `executor.execute_command`, the `services.commands.execute_command` re-export, `EntityStore.set_prop` and `OpenSCADEngine.render`, and asserts after the test that each was called **0 times** and that the model is unchanged.

**Mutation check** (scratch script, not committed): with `verify_provenance` replaced by a no-op, 16 subtests failed — exactly the cases only provenance catches. The cases the Day-27 contract catches first still passed, as expected. Same check for the hardening (scratch script): with `verify_property` replaced by a no-op only `test_property_must_match_the_users_wording` failed; with `verify_target` replaced by a no-op only `test_selected_wall_must_be_the_target` failed.

**Day-29 test change:** `test_only_the_valid_command_of_a_mixed_reply_is_returned` proposed `Thickness 250 mm` while using the default prompt "Make the selected wall 3.2 m high". Day 30 now correctly rejects that combination, so the test was given the matching prompt "Make the wall 250 mm thick."; its assertions are unchanged.

**Full backend suite: 66 tests OK** (the 60 from before the hardening — Day 25 workflow, Day 27 contract, Day 28 transaction including the real-OpenSCAD tests, Day 29 intent parsing, Day 30 — all unchanged and passing, plus the 6 hardening tests), none skipped. No existing test needed changing for the hardening: every accepted case already named its property and targeted the selected wall. `backend/data/model.json` SHA-256 `23de08c2…be345f` was unchanged before and after.

## 9. Live Ollama smoke test (integration evidence, not an evaluation)

Real `llama3.2:3b`, `temperature 0`, through the real `AIChatService`, on a scratchpad copy of the model (selected wall `wall-001`, whose quantities are all 1111 mm). Single runs; timings are preliminary integration timing only.

This smoke test was run **before** the hardening checks (§7a, §7b) were added and was not re-run. No live Ollama run has been made with the final checks in place. What the recorded results do and do not show about those checks:

- **Property (§7a):** in every accepted proposal, the recorded property matches the request's wording (4 metres *long* → Length; 250 mm, 0.05 m, 0.08 m *thick* → Thickness). Every rejected proposal was rejected by a check that runs earlier (the Day-27 contract or the quantity checks), so its recorded reason would not change. This is reasoning from the recorded proposals, not a new run.
- **Target (§7b):** none of the accepted prompts refers to the selection ("Make the wall …"; none says "selected", "selection" or "this wall"), so the selected-target check would not apply to them. The tables do not record target GUIDs. Each accepted target was validated by the contract as an existing IfcWall, but whether it was the selected wall is not established by these records.

*Correction (2026-10-09, before commit):* an earlier draft of this paragraph said every accepted proposal "targeted the selected wall". The live-run records contain no target GUIDs, so that claim was unsupported; it was replaced by the paragraph above.

**Required set**

| Request | LLM proposal | Decision | Deciding layer |
|---|---|---|---|
| Make the selected wall 3.2 m high | Height 3200.0 **m** | Rejected `value_out_of_range` | Day-27 bound |
| Make the wall 4 metres long | Length 4 m | **Accepted**: 4000 mm | contract + provenance |
| Make the wall 250 mm thick | Thickness 250 mm | **Accepted**: 250 mm | contract + provenance |
| Make the wall 0.05 m thick | Thickness 0.05 m | **Accepted**: 50 mm | contract + provenance |
| Make the wall longer | no command | No change proposed | model |
| Make the wall 500 metres long | Length 500 m | Rejected `value_out_of_range` | Day-27 bound |
| Create a door | no command | No change proposed | model |
| Make the wall taller | Height **1111 m** (invented) | Rejected `value_out_of_range` | Day-27 bound |

**Additional prompts, chosen so that model errors fall below the 100 m bound**

| Request | LLM proposal | Decision |
|---|---|---|
| Make the wall 0.08 m thick | Thickness 0.08 m | **Accepted**: 80 mm |
| Make the selected wall 2.8 m high | Height 2800 **m** | Rejected `value_out_of_range` (bound) |
| Make the wall 2 m longer | Length **2 m** (absolute) | Rejected `relative_change_unsupported` |
| Make the wall 30 cm thick | Thickness **30 mm** | Rejected `no_explicit_quantity` |
| Make the wall thicker | Thickness **1111 mm** (invented) | Rejected `no_explicit_quantity` |
| Make the wall 3 m high and 4 m long | Height 3 m | Rejected `ambiguous_quantity` |

Observations:

- In three live cases ("2 m longer", "30 cm", "thicker") the model produced a command that **passes the Day-27 contract but is wrong**. Only the provenance check rejected them.
- The model's "value converted, unit kept" error recurs on "selected wall … m high" prompts (3.2 m, 2.8 m). The boundary fails safe — the request is rejected and nothing wrong is proposed — but a legitimate request is not served. Day 30 does not make the model more accurate.
- "Make the wall taller" and "thicker" copy the wall's current value (1111) as an invented value.

Across both runs, `execute_command`, `set_prop` and `render` were each called 0 times. The model copy and the real `model.json` were unchanged.

## 10. Execution remains disabled

No code added on Day 30 calls `execute_command()`, `EntityStore.set_prop()`, `/api/properties/`, `OpenSCADEngine`, OpenSCAD or `/api/scad/render`. Accepted proposals are only returned and shown as text. The Day-28 transaction path and the geometry pipeline are unchanged.

## 11. Limitations

- **Property wording is a fixed English keyword list** (§7a). Other phrasings ("in elevation", "deep", "span") are rejected as `no_explicit_property`; a request that mentions a second property word for another reason ("the long wall … high") is rejected as ambiguous.
- **Target is checked only when the user refers to the selection** ("selected", "selection", "this wall"). "Make the wall 3.2 m high" with a proposal targeting another existing wall still passes: the GUID is only validated to exist and be an IfcWall. The selection is what the frontend sent with the prompt; it is not re-read at confirmation time (Day 31).
- The parser is conservative by design. Number words, comma decimals, thousands separators, other units, and multi-dimension requests are rejected, not interpreted.
- The relative-wording check is a fixed English word list. It can reject legitimate phrasings (e.g. a sentence containing "by" or "more"), and it cannot detect every relative phrasing.
- Exact `Decimal` equality: a proposal that differs only by binary-float noise would be rejected. The contract's `Decimal(repr())` conversion makes this unlikely, and it fails safe.
- The repeated "converted value, original unit" model error is contained, not fixed (false rejections).
- English only; single run; no formal evaluation.

## 12. Remaining for Day 31 (AI → property bridge)

- An explicit user-confirmation step that executes an accepted proposal, through the existing `execute_command()` → `EntityStore.set_prop()` atomic path only, re-validating it (contract + quantity, property and target provenance) at confirmation time.
- Decide whether every request without a selection reference must also target the selected wall before execution.
- Remove or neutralise the frontend's legacy `CreateComponent → /api/scad/render` branch and harden `/api/scad/render` itself (still deferred; the server no longer returns such commands).

## Files changed (Day 30)

- `backend/services/commands/provenance.py` (new; `verify_property` and `verify_target` added by the hardening, `verify_provenance` unchanged)
- `backend/services/commands/contract.py` (eight `RejectionReason` members only)
- `backend/services/commands/__init__.py` (exports)
- `backend/services/llm/chat_service.py` (calls `verify_provenance`, `verify_property` and `verify_target` after `validate_command`; passes the prompt and the selected GUID)
- `backend/tests/test_safety_boundary.py` (new)
- `backend/tests/test_llm_intent_parsing.py` (one fixture prompt, see §8)
- `Steps/Day-30-safety-boundary.md` (new)

## Subsequent updates (2026-10-17)

*Added during the Day 51 documentation update. Everything above is left as originally recorded.*

- **Status.** The "awaiting review (not committed)" status above is out of date. Day 30 was committed together with Day 29 on 2026-10-09 as `5f1cf24` (code and tests) and `49f9718` (documents). The local remote-tracking reflog records the push of `49f9718` to `origin/main` on 2026-10-09. On 2026-10-17 both commits are ancestors of `origin/main` (`8561338`).
- **Correction to §5 and §11 (found on Day 36).** §5 says a unit other than mm/m leaves the request "with no explicit quantity", and §11 says thousands separators and other units are "rejected, not interpreted". That holds only when the request contains no other mm/m quantity. When it does, the parser read only part of the request and validation accepted it if the model agreed: "3 200 mm" was read as 200 mm, "3 m 20 cm" as 3 m, and "- 2.5 m", "−2.5 m" and "–2.5 m" as +2.5 m. The Day 37 completeness guard (`764db95`) now rejects these; see `Steps/Day-36-quantity-parser-investigation.md` and `Steps/Day-37-quantity-completeness-guard.md`.
- **Later additions to the provenance checks:** `verify_selected_target()` (Day 31), `verify_entity_reference()` (Day 34, `fc1fa7b`) and `unaccounted_quantity_material()` (Day 37, `764db95`). The checks described above are unchanged.

# Days 45–48 — Geometry Precision: Assessment, Bounds and Policy

**Status:** ANALYSIS AND VALIDATION ONLY — no application code, export format or validation rule changed; no commit. **Option A** (keep entered precision; document the tolerance) is the **provisionally accepted** policy. It is a documented tolerance, **not a code fix**. The research lead provisionally accepted it in the Day 47 and 48 briefs and provisionally closed the investigation in the Day 49 brief.

*Written retrospectively on 2026-10-17 (Day 51), from the Day 45, 46, 47 and 48 reports (session record of 2026-10-16) and the scripts and result files in that session's scratchpad (`day45-precision/`, `day46-bound/`, `day47-reconcile/`, `day48-audit/`), outside the repository. All results are **historical** and have not been rerun. **The final figures are those of Day 48 (§3).** Earlier figures that were later corrected are listed in §5.*

## 1. Origin

Day 42 (F1) found that dimensions with more than 6 significant digits are rounded in the generated geometry: stored 1234.567 became 1234.57 in the STL. Days 45–48 established the mechanism, bounded the error and recorded a policy.

## 2. Precision pipeline (code trace, Day 45)

| Stage | Precision |
|---|---|
| Input, validation (`dimension_error`: finite, > 0, ≤ 100,000), storage (`json.dump`) | **kept exactly** (IEEE-754 double, JSON round-trip) |
| `.scad` source (`template_renderer.py`, `cube([length, thickness, height])`) | kept exactly |
| Export (`cli_executor.py`: `openscad -o <uuid>.stl`, ASCII STL by default) | **lost here:** float32, then 6 significant digits |
| Stored bounds (`stl_parser.py`, trimesh) | equal the STL (33 of 33 cases) |
| Viewport (three `STLLoader`, Float32 buffer) | one further float32 rounding of the STL text |
| Properties panel | shows the stored value, not the geometry |

**Mechanism, confirmed on Day 46:** an ASCII STL coordinate equals float32(v) printed to 6 significant digits. This model matched OpenSCAD 2021.01 for 87 of 87 targeted values. The tie cases (1234.565 → `1234.56`, 12345.65 → `12345.7`, 99999.95 → `100000`) match "float32, then 6 digits", not "double, then 6 digits".

## 3. Final statement (Day 48)

**Assumptions:**
- **A1:** OpenSCAD converts to float32 with round-to-nearest.
- **A2:** OpenSCAD prints 6 significant digits, correctly rounded.
- **A3:** the viewport computes `Math.fround(parseFloat(t))` (V8).
- **A4:** the wall's minimum corner is exactly 0.
- **A5:** 2⁻¹²⁶ ≤ v ≤ 100,000 mm.

A1 and A2 are inferred from behaviour, not from OpenSCAD's source.

**Definitions:** h₆(v) = ½·10^(⌈log₁₀ v⌉ − 6) (half a unit in the 6th significant digit); s(x) = 2^(⌊log₂ x⌋ − 24) (half the float32 spacing).

| Statement | Value | Class |
|---|---|---|
| ASCII STL deviation, tight bound B_At = h₆(v) + s(v) | **≤ 0.05390625 mm** on (0, 100,000] | proven under A1, A2, A4, A5 |
| ASCII STL, exact worst case over 8,192–100,000 mm | **0.05390625 mm** (around 65536.25); the bound is sharp | exhaustive under the model (29,577,217 float32 values) |
| ASCII STL, largest measured in OpenSCAD | 0.0539 mm (at 84668.2539) | observed |
| Viewport deviation, tight bound B_Vt = h₆ + s(v) + s(v + B_At) + 2⁻⁵³·(v + B_At) | **≤ 0.0578125 mm** (+1.1 × 10⁻¹¹) | proven under A1–A5 |
| Viewport, exact worst case over 8,192–100,000 mm | **0.052734 mm** (around 32768.05) | exhaustive under the model |
| Viewport, largest measured in the browser | 0.0508 mm | observed (16 walls, headless Chrome) |
| Whole millimetres, 1–100,000 | **exact** in the STL, the stored bounds and the viewport | proven and tested |
| 0.1 mm grid | **exact** in the STL text and stored bounds; viewport within s(v) ≤ 0.0039 mm | proven under A1, A2, A5; 1,000,000-value sweep; OpenSCAD samples |

The conservative forms are also valid but looser. B_A = h₆(v) + 2⁻²⁴·v has a ceiling of 0.0559605 mm; B_V = B_A + (2⁻²⁴ + 2⁻⁵³)·(v + B_A) has a ceiling of 0.0619209 mm.

Values below 8,192 mm were not searched exhaustively; the proof covers them, and the Day 46 sweeps found no violations there.

**Scope.** These statements apply only to:
- axis-aligned box walls anchored at the origin;
- OpenSCAD 2021.01 ASCII STL;
- three.js in a V8-based browser.

They do not cover translated, rotated or boolean geometry, other OpenSCAD versions, or accepted inputs below 2⁻¹²⁶ mm. They do not establish IFC interoperability.

## 4. Policy options

| Option | What it means | Status on 2026-10-17 |
|---|---|---|
| **A** | Keep entered precision. Claim exactness for whole millimetres (and 0.1 mm in the STL text); state the bounds in §3 for everything else | **Provisionally accepted as the MVP policy.** Documented here. No regression test for the bounds has been added |
| **B** | Enforce an input resolution (0.1 mm or 1 mm) at validation | **Not implemented.** A data-policy choice; at 0.1 mm the STL text would equal the stored value for every accepted input |
| **C** | Export binary STL (`--export-format binstl`) | **Not implemented.** Evaluated outside the app's pipeline on Day 46: worst error ≤ 0.0039 mm (about 13× smaller), viewport identical to the backend parse, files about 2.5× smaller; `trimesh` and the browser `STLLoader` read it. A possible later task |

## 5. Corrections to earlier figures

Each report was corrected by a later one. The corrected values are those in §3.

| Stated on | Earlier statement | Corrected on | Correction |
|---|---|---|---|
| Day 45 | ASCII error "at most 0.05 mm", "relative ≤ ~5 × 10⁻⁶" | Day 46 | Observed maximum 0.0539 mm; relative maximum 5.018 × 10⁻⁶ |
| Day 46 | Ceilings "≈ 0.054 mm" (ASCII) and "≈ 0.058 mm" (viewport) attributed to the stated formulas | Day 47 | Those are the **tight** ceilings (using s(v)); the stated formulas' ceilings are 0.0560 and 0.0619 mm |
| Day 46 | Formula with ⌊log₁₀ v⌋ | Day 47 | Jumps to 0.506 mm at exactly v = 100,000; replaced with ⌈log₁₀ v⌉ − 6 |
| Day 46 | Viewport formula ½·10^(…) + 2·2⁻²⁴·v | Days 47–48 | Missing terms (2⁻²⁴·B_A, and the text-to-double rounding 2⁻⁵³) added; tight ceiling 0.0578125 mm |
| Day 46 | "A translated wall could reach twice the bound" | Day 47 | Error depends on coordinate magnitudes: \|ΔD\| ≤ B(\|x\|) + B(\|x + L\|). In OpenSCAD, a 0.107 mm wall at x ≈ 90 m exported as 0.2 mm. The current template does not translate walls |
| Day 47 | Conservative viewport formula paired with the tight ceiling 0.058 mm | Day 48 | Tight formula B_Vt stated explicitly with its own ceiling |

## 6. Open decisions (not implemented)

- **Tiny inputs:** values in (0, 2⁻¹²⁶) mm are accepted by validation (`> 0`) but fall outside the bound; 10⁻⁴⁶ exports as 0. This is a validation-policy decision.
- **Translated, rotated or boolean geometry:** a future design question.
- **Options B and C:** see §4.
- **Display:** the Properties panel can show 1234.567 while the geometry is 1234.57, and nothing in the UI shows the difference.

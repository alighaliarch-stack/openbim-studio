# Day 41 — Viewport Readability (Z-up lighting and grid)

**Status:** COMPLETE — committed `b65e3cb` "Day 41: Light and grid the viewport for the Z-up scene" (2026-10-17), pushed to `origin/main` on 2026-10-17.

*Written retrospectively on 2026-10-17 (Day 51). The implementation is in the repository. Measurements are **historical** (Day 41 report, session record of 2026-10-16; scripts, JSON and logs in that session's scratchpad `day41-viewport/`).*

## Objective

Fix Day 38 finding D6: camera-facing wall faces rendered almost black, the selection orange was visible mainly on edges, and the grid stood vertically.

## Root cause

The scene, camera and model data are consistently Z-up; only the light and the grid assumed Y-up:
1. The only light was a `HemisphereLight`, whose sky direction defaults to +Y. The −Y faces, which face the default camera, got only the dark ground colour `0x444444`.
2. three.js 0.160.1 uses physical light units and tone mapping is off, so intensity 1.2 with no directional light left vertical faces dim and indistinguishable.
3. `GridHelper` lies in the XZ plane, which is vertical in a Z-up scene.

## Change (`frontend/src/components/viewport/SceneManager.ts`, +17/−3, constructor only)

- `HemisphereLight(0xffffff, 0x888888, 1.5)` with `position.set(0, 0, 1)`: sky from +Z.
- New `DirectionalLight(0xffffff, 2)` at (1, −2, 3), so front, side and top are shaded differently.
- `GridHelper` with `rotation.x = Math.PI / 2`, putting it in the XY ground plane.

Material, highlight colours, camera, picking, focus and geometry loading are unchanged.

## Validation (historical)

On a 5000 × 200 × 3000 mm wall at 1440×900 (pixel at each face's centre):

| Face | Before → after, unselected | Before → after, selected |
|---|---|---|
| Front (−Y) | `#191a1c` (lum 26) → `#90969e` (lum 149) | `#291101` (not orange) → `#d17213` |
| Side (+X) | lum 97 → 136 | `#7c4107` → `#b6620f` |
| Top (+Z) | lum 93 → 182 | `#7c4107` → `#fe8b1a` |

- **Selection:** orange covers 56–64% of the selected wall's screen bounding box, up from 4%.
- **Grid:** now spans x and y at z = 0.
- **Interaction:** selection, deselection, picking, focus and a Height edit behaved the same as before the change.
- **Tests:** `tsc` exit 0; `workflow.mjs` 84/84; `aiMessages` 8/8. `ai-workflow.mjs`: **the first run aborted at 54/55** (a timeout at C7b's "manual-edit mesh" wait); two reruns passed 69/69. That timeout is the intermittent issue followed up on Days 42–44.
- **Re-verified on 2026-10-17 (Day 50B), at HEAD `8561338`:** `workflow.mjs` 84/84; `ai-workflow.mjs` 69/69. The pixel measurements were not repeated.

## Remaining limitations

- The key light is fixed in world space. Seen from the +Y / −X side, the vertical faces are a flat mid-grey (lum 105) and the edge between them is less distinct.
- The grid sits at z = 0, coplanar with the wall's bottom face; no flicker was seen.
- Measured in headless Chrome with software rendering only.
- Not addressed: auto-fit, storey elevation, edge outlines.

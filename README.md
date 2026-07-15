# OpenBIM Studio — MVP Skeleton

A compilable project skeleton for the OpenBIM Studio 4-week MVP, generated
from the Software Design Specification (`OpenBIM_Studio_SDS_Complete.docx`)
and Software Requirements Specification (`SRS_OpenBIM_Studio_MVP.pdf`).

**This is a skeleton, not a finished product.** Every module is scaffolded
with correct types, class/interface shapes, imports, and API wiring, but
business logic is intentionally left as `TODO` in the places the SRS marks
as "Code scaffold provided" for the implementer to fill in (SCAD template
edge cases, LLM provider call, STL geometry loading, etc). What *is* wired
end-to-end: routing, dependency injection, error handling, and the full
request/response contracts between all 8 in-scope modules.

## Module scope (8 of 13, per SRS Section 1.2)

| Module | Layer | Status |
|---|---|---|
| M01 GUI Layer | Frontend | `App.tsx` + `components/layout/` |
| M02 Model Tree | Frontend | `components/modeltree/` |
| M04 Selection Manager | Frontend | `context/SelectionContext.tsx`, `selection/` |
| M05 Property Manager | Frontend | `components/properties/` |
| M06 Geometry Manager | Frontend | `components/viewport/` |
| M08 OpenSCAD Engine | Backend | `services/openscad/` |
| M09 AI Chat Module | Backend | `services/llm/` |
| M10 IFC Semantic Layer | Backend | `services/ifc/` |

Excluded from MVP (see SRS Section 1.2 "Out of Scope"): M03 Layer Tree, M07
Command Interpreter (undo/redo), M11 IFC Export, M12 IDS Validation, M13
Digital Twin, and any database (model.json file is the only persistence).

## Quick start

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY or OPENAI_API_KEY
python main.py
# -> http://localhost:8000  (docs at /docs)
```

Note: `POST /api/scad/render` requires the `openscad` CLI to be installed
and on `PATH` (or set `OPENSCAD_BINARY` to an absolute path). Without it,
the endpoint returns a clean `{"success": false, "error": "..."}` rather
than crashing.

### Frontend

```bash
cd frontend
npm install
npm run dev
# -> http://localhost:5173  (proxies /api to :8000)
```

## What's implemented vs. stubbed

- **Fully wired**: FastAPI app + routers + DI, EntityStore (M10) CRUD +
  spatial tree + JSON persistence, OpenSCAD render pipeline orchestration
  (M08), AI chat orchestration (M09) with graceful error handling, React
  contexts (Model/Selection/Viewport), Three.js scene lifecycle (M06),
  full REST client (`api/client.ts`), and property-edit optimistic UI (M05).
- **Stubbed (`TODO`, raises `NotImplementedError` or documented gap)**:
  the actual LLM provider HTTP call in `services/llm/llm_client.py`; real
  STL parsing/rendering in the viewport (uses bounding-box placeholders
  per MVP scope); command execution for `EditProperty`/`UpdateSelection`
  AI commands; `applyTransform` on the viewport (Phase 2 gizmos).

## Verification performed

- `python3 -m py_compile` on every backend `.py` file — clean.
- Backend imported and exercised end-to-end via `fastapi.testclient
  .TestClient` against every route (`/api/health`, `/api/model`,
  `/api/tree`, `/api/properties/{guid}`, `/api/scad/render`,
  `/api/ai/prompt`), including error paths (unknown GUID, unknown
  component, missing OpenSCAD binary, missing LLM API key).
- `npx tsc -b` on the full frontend — zero type errors.
- `npx vite build` — production bundle builds successfully.

## Repository structure

```
openbim-studio/
├── backend/
│   ├── main.py                  # FastAPI entrypoint + DI wiring
│   ├── core/                    # config.py, persistence.py
│   ├── api/                     # REST routers + DTOs
│   ├── services/
│   │   ├── ifc/                 # M10 IFC Semantic Layer
│   │   ├── openscad/            # M08 OpenSCAD Engine
│   │   └── llm/                 # M09 AI Chat Module
│   ├── templates/                # wall/column/door .scad.j2
│   ├── data/model.json           # seed model
│   └── requirements.txt
└── frontend/
    └── src/
        ├── types/                 # shared entity/command/API types
        ├── api/client.ts          # typed fetch wrapper
        ├── context/                # Model / Selection / Viewport
        ├── hooks/                  # useSelection, useModel
        ├── selection/              # M04 multi-select logic
        └── components/
            ├── layout/             # M01 AppShell, Toolbar
            ├── modeltree/          # M02
            ├── viewport/           # M06 (Three.js)
            ├── properties/         # M05
            └── aichat/             # M09
```

See `ARCHITECTURE.md` for module responsibilities and data flow.

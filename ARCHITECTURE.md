# Architecture

Source of truth: `OpenBIM_Studio_SDS_Complete.docx`,
`3-OpenBIM_Studio_Module_Architecture_Complete.docx`, `SRS_OpenBIM_Studio_MVP.pdf`.

## Data flow (property edit → geometry re-render)

```
User edits property (M05 PropertyPanel)
  -> useModel().editProperty()
     -> optimistic local update (ModelContext.applyPropertyEdit)
     -> POST /api/properties/{guid}                [api/properties.py]
        -> EntityStore.set_prop()                   [services/ifc/entity_store.py]
           -> in-memory update + model.json persist  [core/persistence.py]
           -> notify subscribers
  -> (frontend) caller may follow up with:
     -> POST /api/scad/render                       [api/scad.py]
        -> OpenSCADEngine.render()                   [services/openscad/engine.py]
           -> TemplateRenderer.render()               (Jinja2 .scad.j2 -> .scad)
           -> CLIExecutor.render_to_stl()             (subprocess -> openscad CLI)
           -> StlParser.parse_bounds()                (trimesh -> bounds)
     -> SceneManager.loadMesh(guid, bounds)          [components/viewport/SceneManager.ts]
```

## Data flow (AI chat → command execution)

```
User sends prompt (M09 AIChatPanel)
  -> POST /api/ai/prompt                            [api/ai.py]
     -> AIChatService.send_prompt()                   [services/llm/chat_service.py]
        -> ContextBuilder.build(selected_guid)         (spatial tree + selection -> JSON)
        -> LLMClient.complete(AIRequest)                (TODO: real provider call)
        -> OutputParser.parse(raw_text)                 (validates against AIResponse schema)
  -> frontend receives AIResponse{intent, commands, text, suggestions}
  -> AIChatPanel.executeCommands(): CreateComponent -> POST /api/scad/render -> refetch()
```

## Module → file map

| Module | Responsibility (per SDS) | Files |
|---|---|---|
| M01 GUI Layer | Top-level shell, layout, routing | `frontend/src/App.tsx`, `components/layout/` |
| M02 Model Tree | Spatial hierarchy browser | `components/modeltree/` |
| M04 Selection Manager | Click-to-select sync (tree ↔ viewport) | `context/SelectionContext.tsx`, `selection/SelectionManager.ts` |
| M05 Property Manager | PSet read/edit | `components/properties/` |
| M06 Geometry Manager | Three.js scene, raycasting, mesh loading | `components/viewport/` |
| M08 OpenSCAD Engine | Template → CLI → STL → bounds | `backend/services/openscad/` |
| M09 AI Chat Module | NL prompt → context → LLM → commands | `backend/services/llm/` |
| M10 IFC Semantic Layer | Entity graph, spatial tree, persistence | `backend/services/ifc/` |

## Dependency graph (MVP subset, per SDS 8.1)

```
M02 Model Tree     -> M04 Selection, M10 IFC (via REST)
M04 Selection      -> M02, M06 (shared React context)
M05 Property Mgr   -> M04 Selection, M10 IFC (via REST)
M06 Geometry Mgr    -> M04 Selection, M10 IFC (via REST)
M09 AI Chat         -> M08 OpenSCAD, M10 IFC (server-side, direct calls)
```

Note: In the full 13-module architecture, most of these dependencies run
through M07 Command Interpreter for undo/redo support. M07 is out of MVP
scope (SRS 1.2), so the frontend/backend call each other more directly;
see the `TODO(Phase 2)` markers in `api/ai.py` and
`components/aichat/AIChatPanel.tsx` for where M07 would be inserted.

## Known MVP simplifications (tracked as TODOs in source)

1. **No real STL rendering** — the viewport draws a bounding-box placeholder
   (`THREE.BoxGeometry`) sized from `GeometryBounds` rather than parsing
   actual mesh triangles client-side.
2. **No LLM provider wired** — `services/llm/llm_client.py::complete()`
   raises `NotImplementedError`; the API layer already handles this
   gracefully (returns a friendly `AIResponse`, not a 500).
3. **No undo/redo** — M07 Command Interpreter is out of scope; all writes
   are immediate and irreversible.
4. **No multi-user / auth / DB** — `model.json` is the only persistence,
   single active editor assumed.

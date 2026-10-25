# Visual workflow editor and execution graph

User authorized direct development in E:/server-ai. Preserve existing configuration and single-path execution semantics, including condition branches. Do not add parallel scheduling or import any developer MCP.

- [x] Add tests for graph layout persistence, explicit entry, and execution snapshots/transitions.
- [x] Persist optional node labels, positions and entry ID; publish bounded workflow SSE snapshots with running/completed/failed node state and inner tool events.
- [x] Implement React Flow canvas, drag/connect/delete, labeled conditional branches, type-specific configuration, graph validation and JSON import/export.
- [x] Replace JSON-only workflow editor and inaccurate array-order preview. Add live execution and saved-run graph views.
- [x] Verify backend suite, graph unit tests, browser drag/connect/save/reload/branch execution, production build and independent review.

Verification: 35 backend tests passed, 5 graph unit tests passed, 7 browser end-to-end tests passed; production build succeeded. Visual screenshots checked for editor and executed branch. Independent review prompted per-node argument draft preservation and composable deletion updates; browser regressions verify JSON import and invalid argument preservation. Runtime graph keeps immutable execution snapshots, supports cancellation and marks interrupted nodes. Existing API /run and legacy workflow order remain compatible. No paid model request was used for verification. Vite still warns about the existing large UI vendor chunk.

Compatibility: old workflows start at first array element if entry_id is absent. One next edge per ordinary node; condition nodes may have next and otherwise. Cycles forbidden. Running graph comes from a saved snapshot so later edits cannot rewrite history.

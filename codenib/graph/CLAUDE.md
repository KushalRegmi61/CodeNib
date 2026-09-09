# graph/ — rules

`CodeGraph` (`code_graph.py`) is an [igraph](https://igraph.org)-backed semantic
graph. Source vertices are directories/files/symbols with containment and
reference edges. Phase 1a also permits a persisted architecture overlay:
`workspace -> project -> file -> symbol`, with manifest dependency edges.
`workspace_enrichment.py` owns overlay construction, ownership, digests, and
validation; it does not create a second graph store. `roi_subgraph.py`,
`traverse_graph.py`, and `dependency.py` remain source-graph consumers.

## Conventions

- **Node / edge types are centralized in [`codenib/types.py`](../types.py).**
  Source vertices are `directory`, `file`, `symbol`, `class`, `function`,
  `method`, and `field`; architecture vertices are `workspace` and `project`.
  Edges include `contain`, `reference`, `import`, `type-use`, and
  `depends_on_manifest`. Use the `NODE_TYPE_*` / `EDGE_TYPE_*` constants and
  predicates — never hard-code string literals.
- **Persisted-graph schema is versioned.** `_SCHEMA_VERSION` in `code_graph.py`
  is 6. `load_graph()` rejects mismatches, so pre-Phase-1a graph.pkl files
  must be regenerated. Keep this name distinct from `builder_schema`,
  `query_surface_schema_version`, and `workspace_enrichment_version`.
- **Architecture mutation is validated.** Use
  `CodeGraph.add_architecture_edge()` rather than `_add_edge()` for workspace
  or project edges. It validates existing endpoints and rejects anchored or
  unknown architecture edges before `_add_edge()` can mint a typeless vertex.
  `remove_architecture_overlay()` must rebuild name/file/edge/range indexes.
- **Native parity is layered.** The native fact query index recognizes
  architecture records and valid unanchored architecture edges, but excludes
  them from source symbol/reference/range indexes. Phase 1b FactBatchBuffer v2
  rows may carry workspace/project attributes; source-only query receipts
  remain schema 1 and must not change when architecture metadata changes.
- **Source consumers stay isolated.** Use `SOURCE_DEPENDENCY_EDGE_TYPES` for
  traversal, dependency, ROI, retrieval, and external source consumers. The
  manifest dependency edge is architecture data even though it remains in the
  global dependency-type union.

## Incremental patching (`graph/incremental/`)

- One patcher per language (`patcher_python.py`, `patcher_go.py`,
  `patcher_cpp.py`, `patcher_rust.py`, `patcher_ts.py`), all on
  `patcher_base.py`, driven by `lsp_client.py` + `change_mgr.py`.
- A new language needs a `patcher_<lang>.py` that subclasses the base; don't
  fork the dispatch logic in `graph_patcher.py`.

# Monorepo-Aware Context Engine Roadmap

This document is the implementation authority for the monorepo context engine.
The selected architecture is a literal workspace/project overlay in the
existing Python `CodeGraph`; it is not a sidecar graph or a second database.

## Phase 1a — Python persistence and native tolerant-skip

Phase 1a is complete when a graph build persists one four-tier graph:

```text
workspace -> project -> file -> symbol
```

The workspace and project vertices, containment edges, manifest dependency
edges, file/symbol `project_id` attributes, and bounded workspace metadata are
written to the existing `graph.pkl` and `repo_manifest.json` artifacts. The
workspace scan is performed once after all language graphs have been merged.

Supported discovery includes Bazel, Turbo/Node/pnpm, Python, Cargo/Rust, Go,
and deterministic heuristic ownership. The adapter registry is the authority;
Go must be present in `workspace/inventory.py`, `workspace/registry.py`, and
the scanner's detected-system map.

### Persistence contract

- `L_workspace` is represented by a vertex with `type="workspace"` and a
  stable `workspace://...` name.
- `L_project` is represented by a vertex with `type="project"` and a stable
  `project://...` name.
- File and symbol vertices receive `project_id`; synthetic-root ownership is
  a valid fallback value but is counted as unowned.
- `contain` edges connect workspace to project and project to file. Existing
  source containment remains unchanged.
- `depends_on_manifest` connects resolved project dependencies. Unresolved
  dependencies and self-dependencies, including self-via-alias, do not create
  edges and produce bounded diagnostics where applicable.
- Empty manifest-derived projects remain in the graph with zero selected-file
  counts, so source selection does not change project identity.
- `is_shared` means at least one distinct external consumer was observed from
  source dependency edges or manifest dependency edges. `sharing_complete` is
  a completeness signal; false means unknown/lower-bound, not unshared.

The mutating boundary is `CodeGraph.add_architecture_edge()`. It validates
both endpoint vertices before delegating to `_add_edge()`, because `_add_edge`
can otherwise create missing typeless vertices. Architecture edges cannot have
anchors. Overlay replacement deletes architecture vertices in reverse index
order, rebuilds indexes, invalidates caches, and rechecks range indexes.

`CodeGraph._SCHEMA_VERSION` is 7. Schema 7 adds bounded persisted workspace
context and normalized manifest dependency evidence. This is deliberately distinct from
`builder_schema`, `query_surface_schema_version`, and
`workspace_enrichment_version`. Old graph schema 6 artifacts must fail with a
graph-schema-specific stale-cache error.

### Build and incremental ordering

The full-build sequence is:

```text
merge language graphs
-> scan/enrich workspace and ownership
-> apply RepositorySourceSelection
-> prune dangling architecture edges
-> retain empty projects
-> compute final counts/digests
-> save graph and publish metadata
```

`_artifact_identity_for_selection()` remains configuration-based; topology and
manifest bytes are not placed in the pre-build identity. The compiler passes
previous symbol-graph metadata separately to incremental builders.

Before the incremental language loop, `_patch_graph()` performs a cheap
`git diff --name-status` check of manifest basenames. It does not walk the
repository when no manifest path changed. A manifest change triggers one
workspace scan:

- topology digest changed: return `None` and force a full source rebuild;
- metadata digest changed only: re-enrich and republish graph metadata without
  source rebuild or BM25/vector re-embedding;
- neither changed: preserve the normal no-op/incremental path.

The `changed_total == 0` return is after this pre-check, so manifest-only
commits cannot be lost.

### Digests and metadata

`topology_digest` is reconstructible from graph data and includes workspace
identity, stable project attributes, workspace-to-project edges, and manifest
dependency edges. It excludes project-to-file edges because those depend on
source selection. `architecture_digest` includes the final architecture
overlay, including project-to-file containment after source pruning.

`repo_manifest.json` stores a bounded workspace summary: at most 100 project
summaries and 100 diagnostics, with `projects_truncated` and
`diagnostics_truncated` flags. Counts and digests are retained regardless of
truncation. Full vertices and edges remain in `graph.pkl`.

### Source consumers and native boundary

`SOURCE_DEPENDENCY_EDGE_TYPES` isolates existing source traversal, dependency,
ROI, retrieval, and OrcaLoca behavior from `depends_on_manifest`. The
dependency graph layer remains source-only; the architecture layer includes
architecture containment and manifest dependencies; the all layer includes
both. Consumers filter architecture endpoints because `contain` is shared.

Phase 1a native work is tolerant classification only. Native code recognizes
workspace/project records, permits valid architecture containment and manifest
dependency edges, excludes architecture data from symbol/reference/range
indexes, and rejects anchored or invalid architecture edges. It does not add
Python-only project attributes to native rows, change fact-buffer row sizes, or
change the source query-surface schema. The source query receipt hashes the
source compatibility projection; Python stores a separate architecture digest.

### MCP cold loading

MCP loads workspace metadata from the persisted symbol-graph entry and never
rescans on cold start. It reports:

- `available` when metadata and graph topology/architecture digests agree;
- `unavailable` when a schema-7 graph has no workspace metadata (source-only
  graph or mixed manifest/graph generation);
- an integrity error when metadata exists but graph digests disagree;
- the normal stale graph-schema error for schema 5.

`get_manifest` exposes the bounded workspace status. Portable artifact config
is unchanged in Phase 1a.

## Phase 1b — native workspace framing and project-aware queries

Phase 1b consumes the persisted Phase 1a `graph.pkl`; it does not introduce a
second graph store. Its contracts are independent: graph schema 7,
workspace-enrichment version 2, FactBatchBuffer ABI/schema 2, source
query-surface schema 1, and project query-surface schema 3.

The Python graph remains the correctness path. The v2 compatibility frame
extends vertex rows with project/workspace identity, paths, display/kind, and
explicit architecture booleans; edge rows remain anchor-aware and reject
anchors on workspace/project edges. FactBatchBuffer v1 is rejected with an
explicit native-contract mismatch, while source-only query behavior retains
its schema-1 receipt.

The complete v3 project query surface canonically frames source and
architecture vertices/edges, including ownership, sharing metadata, and
normalized manifest evidence. Compiler receipts publish both source and
project-surface digests together with topology, metadata, and architecture
digests. MCP cold loading validates those receipts without rescanning.

The MCP surface now provides bounded project dependency roll-ups,
`find_projects_using`, and optional `project_id` filters for BM25, semantic,
context, and explore retrieval. Filtering is applied before ranking and old
retrieval artifacts without ownership metadata fail closed as unavailable for
project filtering; unrestricted search keeps its established response shape.

Project ownership is resolved once per compiler generation and passed to the
graph, sparse, and dense builders. Metadata-only ownership changes reuse
content hashes/embeddings; topology changes invalidate project metadata and
retrieval views. Native project aggregation remains capability-gated until a
digest-verified native overlay merge consumer is available.

## Required verification

The unit tier must cover scanner adapters and deterministic digests; enrichment
must cover overlay replacement, source selection, empty projects, synthetic
root ownership, sharing, dependency resolution, and canonicalization failure.
Compiler tests must cover exactly-once multi-language enrichment, manifest
add/delete/rename detection, manifest-only topology rebuilds, metadata-only
updates without re-embedding, and no-op updates without an inventory walk.

Graph persistence tests must cover schema 7 round trips, schema-6 rejection,
workspace-context validation, manifest-evidence ordering and digest changes,
endpoint validation,
anchored-edge rejection, architecture exclusion from ranges, and index
rebuild after overlay removal. Source consumer fixtures must prove byte-
identical source-only traversal, ROI, dependency, and retrieval results before
and after enrichment. MCP tests must cover cold loading, missing metadata, and
digest mismatch. Native tests must cover tolerant architecture classification
and unchanged source query behavior without new row sizes.

The implementation files are:

- `codenib/graph/workspace_enrichment.py` — overlay construction, ownership,
  sharing, pruning, digests, summaries, and validation;
- `codenib/graph/code_graph.py` — architecture mutation boundary and schema 7;
- `codenib/compiler/index_builders.py` and `index_compiler.py` — full and
  incremental integration;
- `codenib/workspace/` — manifest inventory, adapters, models, and scanner;
- `codenib/mcp/context.py` and `server.py` — cold-load status and manifest;
- `core/fact_query_index.*`, `core/code_graph.h`, and pybind bindings — native
  tolerant-skip only.

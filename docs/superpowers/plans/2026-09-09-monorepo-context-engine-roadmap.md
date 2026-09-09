# Monorepo-Aware Context Engine — Code-Level Roadmap (Phase 2 Ingestion, Phase 3 Query)

> For the local coding agent: this doc is the build authority. Every file:line below was read at HEAD 61a9ab2f (Sep 2026). Section 0 lists where the source draft was wrong — follow this doc, not the draft, on those points.

**Goal:** teach CodeNib workspace/project structure (Bazel, Turbo/pnpm/npm, Cargo, Python, Go) so agents can ask "what uses shared-lib X" and "which projects break if f changes", without a second graph store.

**Architecture (one paragraph):** do NOT add `L_workspace`/`L_project` vertices to the igraph. Persist a `projects.json` sidecar inside the existing `symbol_graph` view directory, resolve `project_id` by longest-prefix match at query time, and roll symbol/file edges up to project edges in a new `codenib/graph/project_rollup.py` consumed by `dependency_subgraph(granularity="project")` plus a new `find_projects_using` MCP tool. Zero schema changes, zero C++ work, old caches keep loading. (Rationale in §0.3.)

**Tech stack:** Python 3.10+, PyYAML/TOML (stdlib `tomllib` + PyYAML already a dep), existing igraph `CodeGraph`, existing MCP `ToolSurfaceMCPServer`, existing skill executor pattern.

**Predecessors:** hooks plan `docs/superpowers/plans/2026-09-09-codegraph-auto-update-hooks.md` (branch `feat/codegraph-auto-update-hooks`); maintenance contract `docs/incremental_graph/index.md` (production = reuse current views, rebuild affected; no delta paths).

---

## 0. Verdict on the source draft (what to keep, what to correct)

| # | Draft claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Node types `L_workspace`, `L_project`, `L0`, `L2` as igraph vertices | **REJECT as specified.** Real node constants are `directory/file/symbol/class/function/method/field` (`codenib/types.py:9-19`). There are no workspace/project/file-layer/symbol-layer abstractions; `L0`/`L2` are doc shorthands for chunk levels, not graph types. | `codenib/types.py:9-19` |
| 2 | Edges `calls`, `imports`, `depends_on_manifest`, `contain(workspace→project)` | **REJECT names; KEEP intent remapped.** Only `contain` + `reference` are ever emitted (`codenib/graph/code_graph.py:288,355`); `import`/`type-use` are declared (`codenib/types.py:18-19`) but only TS emits `import` (`codenib/scip_interface/typescript_semantics.py:307`). Zero hits for `calls`/`imports`/`depends_on_manifest` as edge types. Caller/callee questions are answered today over `reference` edges via `DependencyAnalyzer` (`codenib/graph/dependency.py:112,125,135`). | grep `EDGE_TYPE`, `code_graph.py:288,355` |
| 3 | Tag every file/symbol with `project_id` at build time | **REDIRECT to sidecar + query-time resolution.** Python vertices are schema-open, but `core/code_graph.h:41-60` `VertexData` is a FIXED struct, `load_graph` rejects unknown vertex types (`core/fact_query_index.cpp:612-614`), and `code_graph.py:857` fails loud on `_SCHEMA_VERSION` mismatch (currently 5). New attributes = schema 6 + C++ sync + invalidation of every existing cache (including the fresh ai-saas-starter-kit indexes). | `core/code_graph.h:18-71`, `code_graph.py:44,832-862`, `codenib/graph/CLAUDE.md:21` |
| 4 | Manifest declares dep A→B ⇒ edge | **KEEP, but resolve from sidecar, not igraph edges.** No new edge type needed; manifest deps live in `projects.json` and are joined at query time. | new module (§1) |
| 5 | Aggregate `$L_{2,A}→L_{2,B}$` to projects "on the fly without storing duplicate graph" | **KEEP — this is the one idea to preserve verbatim.** Implement as pure function over the loaded graph + sidecar. | §2 |
| 6 | `search_semantic`/`search_bm25` "support path-based queries" | **HALF-TRUE.** Only `search_regex` (`file_glob`, `search.py:283-290`) and `search_zoekt` (`file_filter`, `:364-369`) take path filters; `search_semantic` (`:174-181`) and `search_bm25_impl` (`:238-243`) take none (bm25 has `filter_test` only). Project-scoped search = new optional params, additive and backward compatible. Post-hoc alternative exists: `codenib/ops/filter.py:55-132` (`exclude_paths`, `build_candidate_filters`). | `codenib/mcp/tools/search.py`, `codenib/ops/filter.py` |
| 7 | Weeks 2–3 + Week 4 with "reuse Nx/Bazel helpers if available" | **REJECT external helpers.** Repo vendors no Nx/Bazel/Turbo parsing today (grep: zero hits in `codenib/` for turbo/Bazel/Nx/lerna). Manifest parsing exists scattered: `package.json`+`pnpm-workspace.yaml` (`scip_indexer_ts.py:154,190,646-653`), `pyproject.toml` (`scip_indexer_python.py:208`), `Cargo.toml` workspace/`go.mod` module parsing (`scip_decode_rust.py:162-203`, `scip_decode_go.py:135-139`, `scip_query.py:406-449`). Build ONE detector module on the `languages.py` registry pattern instead. | §1 file list |
| 8 | `find_projects_using`, `identify_project`, per-project search filters | **KEEP all three**, remapped to real seams (§3–§4). Skill pattern exists: `skills/<name>/{config.yaml,skill.md,executor.py}` + `loader.py:31-48` factory. | `codenib/agent/skills/` |

---

## 1. Phase 2 — workspace ingestion (new module, no graph changes)

### 1.1 Create `codenib/workspace/` (new package, mirrors `codenib/languages.py` registry style)

| File | Responsibility |
|---|---|
| `codenib/workspace/__init__.py` | Re-export `detect_workspace`, `Workspace`, `Project`. |
| `codenib/workspace/models.py` | Frozen dataclasses: `Workspace(id="workspace://root", root)`, `Project(id="project://<relpath>", dir, name, manager, is_shared, manifest_deps: tuple[str,...], code_deps: tuple[str,...])`. IDs are strings only — never vertex names (see §0.3). |
| `codenib/workspace/detectors.py` | One `detect_*(root) -> list[ProjectDraft]` function per ecosystem + `detect_workspace(root) -> Workspace` orchestrator. Pure functions of file bytes; no git, no network. |
| `codenib/workspace/resolve.py` | `resolve_project(path, workspace) -> Project | None` = longest-prefix match over project dirs; `project_indegree` for `is_shared`. |
| `codenib/workspace/sidecar.py` | `write_projects_json(view_dir, workspace)` / `load_projects_json(view_dir)`; schema `{schema: 1, workspace, projects[], generated_at, source_fingerprint}`. |

### 1.2 Parser matrix (each detector: files read → projects yielded → internal-dep rule)

- **Node/npm/pnpm/yarn:** root `package.json` `workspaces` (array or `{packages}`) + `pnpm-workspace.yaml` `packages:` globs → expand globs against the tree (do NOT shell out to the package manager); each matched dir with `package.json` = project, `name` field = identity. Internal dep: `dependencies/devDependencies` entry that (a) uses `workspace:` protocol, or (b) names another workspace package. pnpm/tsconfig path aliases are hints only, never authority.
- **Turbo:** `turbo.json` alone defines NO projects — it rides on the underlying npm/pnpm/yarn workspace. Detector: reuse Node detector for membership; additionally parse `pipeline`/`tasks` `dependsOn: ["^build"]` as evidence for `is_shared` (upstream libs), not as dep edges (task deps ≠ package deps).
- **Rust:** root `Cargo.toml` `[workspace] members` (explicit + globs; honour `exclude`) → member dirs with `Cargo.toml` = projects, `[package] name` = identity. Internal dep (authoritative): `[dependencies] foo = { path = "../foo" }`. Version-only deps on the same name are NOT internal.
- **Python:** MANY-TO-ONE problem — several projects, no single workspace file. Sources in priority order: `[tool.uv.workspace] members` (uv), `[tool.hatch]`, `[tool.pdm]`, `[tool.poetry]` groups, plain `pyproject.toml [project] name` per directory, legacy `setup.py`/`setup.cfg`. Internal dep: requirement string matching another project name AND resolvable to its dir (importable top-level ↔ `packages`/`package-dir` mapping); ambiguous names (two projects shipping `utils/`) resolve by directory proximity to the importer, else record `ambiguous: true` and exclude from edges (never guess).
- **Go:** `go.work` `use` directives → projects; else each `go.mod` = project, module path = identity. Internal dep: import path with the workspace module prefix (respect `replace` directives; a replaced path points at the real dir).
- **Bazel:** `MODULE.bazel` (`module(name)`) / `WORKSPACE` root; every dir with `BUILD[.bazel]` = potential project (name = `//path`). Internal dep (authoritative): `deps = ["//other:target", ...]` labels; ignore `@external` / `@pypi` labels. `load()` statements are NOT deps.
- **Fallback (no manifest):** directory heuristics `apps/* packages/* libs/* services/* crates/*` containing source files ⇒ project with `manager="heuristic"`, `manifest_deps=()`. Mark clearly; query layer treats heuristic projects as containment-only (no manifest edges).

### 1.3 Wire into the build (one seam, additive)

`SymbolGraphBuilder` (or the `VectorIndexBuilder`-adjacent compiler step — same `register_default_builders` call site at `codenib/compiler/index_builders.py:2538`) gains: after the graph view publishes, run `detect_workspace(repo_root)` → `write_projects_json(<symbol_graph view dir>/projects.json)`. Cost: file reads + glob expansion only (no LSP/SCIP/embedding). Fingerprint the sidecar inputs (manifest bytes) into the view `metadata` so currency follows the existing per-view identity check (`index_compiler.py:386-397`) with no compiler changes. Dirty tree ⇒ sidecar goes stale exactly like its sibling view (consistent with the "source changed during compilation" rule).

### 1.4 Pseudocode — detection core

```python
def detect_workspace(root: Path) -> Workspace:
    drafts: list[ProjectDraft] = []
    for detect in (detect_bazel, detect_cargo, detect_node,
                   detect_python, detect_go, detect_heuristic):
        drafts.extend(detect(root))          # each returns [] when N/A
    projects = dedupe_by_dir(drafts)         # same dir twice → merge, manifest beats heuristic
    for p in projects:
        p.manifest_deps = tuple(resolve_internal(p, projects))  # per-ecosystem rule, §1.2
    indegree = count_distinct_importers(projects)                 # manifest ∪ code imports (§3.3)
    for p in projects:
        p.is_shared = indegree[p.id] > 1
    return Workspace(id="workspace://root", root=root, projects=tuple(projects))

def resolve_project(path: str, ws: Workspace) -> Project | None:
    cands = [p for p in ws.projects if path == p.dir or path.startswith(p.dir + "/")]
    return max(cands, key=lambda p: len(p.dir), default=None)    # longest prefix; None outside all
```

---

## 2. Phase 3A — project roll-up (pure query layer, `codenib/graph/project_rollup.py` NEW)

```python
def project_edges(graph: CodeGraph, ws: Workspace,
                  kinds: frozenset = DEPENDENCY_EDGE_TYPES) -> dict[tuple[str, str], int]:
    """(src_project_id, dst_project_id) -> edge count. Self-edges dropped."""
    out: dict[tuple[str, str], int] = {}
    for (u, v, etype) in graph.iter_edges():          # use existing edge iterator; reference+import only
        if etype not in kinds:
            continue
        a, b = project_of_vertex(ig.vs[e.source], ws), project_of_vertex(ig.vs[e.target], ws)
        if a is None or b is None or a == b:
            continue
        out[(a.id, b.id)] = out.get((a.id, b.id), 0) + 1
    return out

def project_of_vertex(v, ws: Workspace) -> Project | None:
    # add_file_node stores name=file_path; symbols carry a file attr (ref-only may not).
    path = v["name"] if v["type"] == NODE_TYPE_FILE else (v["file"] or v["name"])
    return resolve_project(path, ws)   # None-safe longest-prefix match

# NOTE: CodeGraph exposes get_neighbors/get_successors/resolve_symbol, but no
# edge iterator — iterate the underlying igraph.Graph (graph.graph.es) directly.
# Edge-type attr is e["type"]; vertex attrs are v["name"]/v["type"]/v["file"].
```

## 3. Phase 3B — MCP surface (additive params + one tool + one skill)

### 3.1 `dependency_subgraph(granularity="symbol"|"project")`
- `codenib/mcp/server.py:396-409` tool def + `codenib/mcp/tools/dependency.py:27` impl: add optional `granularity: str = "symbol"`. At `dependency.py:54-62` (direction normalize) validate; at `:71-91` (dispatch) branch `project` → new `project_impact/project_dependencies` in `codenib/graph/dependency.py` (or `project_rollup.py` reusing `DependencyAnalyzer._bfs` at `dependency.py:192-233`) that maps each BFS hit through `resolve_project` and returns `{"projects": [...], "project_edges": [...], "symbol_evidence": [...]}` — project list primary, symbol hits retained as bounded evidence (mirrors `explore_context` evidence discipline).
- Missing sidecar ⇒ `{"error": "project index not available; rebuild the symbol_graph view"}` (same graceful pattern as `:396` symbol_graph-missing today).

### 3.2 New tool `find_projects_using(name)`
```python
def find_projects_using_impl(ctx, name: str) -> Dict[str, Any]:
    ws = load_projects_json(ctx) or return {"error": ...}
    P = match_project(name, ws)   # package name, dir, or symbol → project via resolve_project
    if P is None: return {"error": f"unknown project or symbol: {name}"}
    users = {a for (a, b) in project_edges(ctx.symbol_graph, ws) if b == P.id}
    users |= {a for a in manifest_dependents(P, ws)}   # manifest edges incl. code-only users missed above? union
    return {"project": P.id, "used_by": sorted(users),
            "manifest_edges": [...], "code_edges": [...]}  # keep the two provenances separate
```
Symbol input: resolve via existing `resolve_symbol` (`code_graph.py:938`) → file → project, then proceed.

### 3.3 `identify_project` skill (no new MCP tool needed)
- `codenib/agent/skills/identify_project/{config.yaml,skill.md,executor.py}` following `hybrid_search/` (`config.yaml:5-14`, `executor.py:57 create_executor(context)->execute()`), loaded by `loader.py:31-48`.
- Executor is pure Python: `resolve_project(file_or_dir)`; keyword input falls back to `search_bm25` top hit → file → project. No LLM, no model.

### 3.4 Project-scoped search (additive optional params)
- `search_bm25_impl(ctx, query, top_k, filter_test)` + `search_semantic(ctx, query, top_k, level, ...)`: add `project: str | None = None`. Implementation: resolve to dir prefix via sidecar, then filter candidates with existing `exclude_paths`/`build_candidate_filters` (`codenib/ops/filter.py:55-132`) — no indexer changes, no re-embedding.
- Unknown project ⇒ empty result + `warning`, never silent full-repo results.

## 4. Edge cases (must-have list for the implementing agent)

1. **Nested workspaces** (repo inside repo, `examples/` with own package.json): nearest manifest wins; child workspace detected independently, parent excludes child dir from its file sweep.
2. **Overlapping globs** (`packages/*` + explicit `packages/foo`): dedupe by resolved dir; manifest-detected beats heuristic on collision.
3. **Python ambiguity** (two projects shipping `top_level/utils.py`): proximity-to-importer wins; else `ambiguous: true`, edge excluded, recorded in sidecar diagnostics.
4. **Manifest/code disagreement** (package.json says dep, no import found, or vice versa): keep provenances separate (`manifest_edges` vs `code_edges`); never synthesize one from the other; `is_shared` uses the union.
5. **Generated/vendored code** (`dist/`, `*.generated.*`, lockfiles, `node_modules`): excluded by existing repository filters; detectors never descend into them for membership.
6. **Bazel externals** (`@pypi//`, `@npm//`): ignored for internal edges; recorded under `external_deps` for display only.
7. **Dirty tree**: sidecar inherits its sibling view's currency — dirty ⇒ stale ⇒ capabilities absent ⇒ MCP returns the standard "not available" error. Same rule as §0 table, no special casing.
8. **No sidecar (old caches, single-package repos)**: every new code path degrades to current behavior; single-project repo ⇒ roll-up is identity (self-edges dropped ⇒ empty project_edges, which is correct, not an error).
9. **Case-insensitive filesystems / symlinked worktrees**: compare via `os.path.normcase` + resolved paths (same discipline as `paths.py:89` state keys).
10. **Performance**: roll-up is O(E) per call over `DEPENDENCY_EDGE_TYPES` only; cap with existing `MAX_DEPENDENCY_EDGES`/`MAX_TOOL_RESULTS` bounds; cache per (manifest fingerprint) in `ServerContext`, never per query.

## 5. Testing (per `test/CLAUDE.md` tiers)

- **Unit (default, no marker):** one test module per detector with inline fixture manifests (Node workspaces obj+array, pnpm globs, Cargo members+exclude, uv workspace, go.work, MODULE.bazel labels, ambiguous-python); `resolve_project` longest-prefix + None cases; roll-up on a synthetic 6-node graph incl. self-edge drop + cross-project counts; receipt-less `find_projects_using` unknown-name error; search filter prefix behavior via `ops/filter` predicates.
- **`integration` (read-only fixture):** build a tiny `test/fixtures/monorepo/` (package.json workspaces + Cargo member + pyproject) and assert sidecar contents end-to-end through `detect_workspace` only (no indexing).
- **`integration_serial`:** full `compile_repo` on the fixture asserting `projects.json` lands beside `graph.pkl` and `dependency_subgraph(granularity="project")` returns the expected project edge. Mutates (writes view dirs) ⇒ serial, never plain integration.
- **Never `slow`:** no LLM, no GPU embeddings anywhere in this program.

## 6. Docs + rollout

- Update: `docs/codegraph.md` (capability note), `docs/graph_query.md`, `docs/graph_cache_usage.md`, `docs/codegraph_hierarchy_model.md` (4-tier conceptual layer — conceptual only, no schema change), `codenib/graph/CLAUDE.md` (roll-up module rule).
- NO changes: `docs/language_capabilities.md` (matrix renders per-language backends; unaffected), C++ `core/`, manifest version (stays 1.2), `_SCHEMA_VERSION` (stays 5).
- Rollout: sidecar ships behind existing `--view symbol_graph` builds (no flag); dogfood on a real monorepo (e.g. one with pnpm + cargo) measuring `status --json` + `find_projects_using` before/after; `make multilang-registry-check` stays green by construction.
- Explicit non-goals (v1): promoting projects to igraph vertices (schema 6 + C++ sync reserved for a later program with a migration story), Turbo task-graph edges, lockfile parsing, remote/registry deps.

## 7. Work breakdown (suggested task order for the executing agent)

1. `codenib/workspace/` models + Node detector + tests.
2. Cargo + Go detectors + tests.
3. Python + Bazel + heuristic fallback + tests.
4. `resolve.py` + `sidecar.py` + tests.
5. Builder hook (sidecar write after symbol_graph publish) + serial fixture test.
6. `project_rollup.py` + unit tests on synthetic graphs.
7. `dependency_subgraph(granularity)` + tests (mock ctx with tiny graph).
8. `find_projects_using` + tests.
9. `identify_project` skill (config/executor/skill.md) + loader test.
10. Search `project` params + tests.
11. Docs updates + dogfood + full unit tier.

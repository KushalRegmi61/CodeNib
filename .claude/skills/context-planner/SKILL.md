---
name: context-planner
description: >-
  Plan repository context before non-trivial coding-agent changes. Use when a
  task spans files, symbols, projects, dependencies, tests, architecture,
  debugging, or uncertain localization. Return bounded, source-linked
  evidence and stop when the required context predicates are satisfied.
---

# CodeNib Context Planner

Use this Skill in the main coding-agent context before non-trivial repository
work. It plans evidence; it does not edit files, run builds/tests, install
packages, mutate indexes, or delegate to subagents in this Phase 1 baseline.

## Contract

First create a compact `ContextContract` from the user request:

```yaml
mode: locate | explain | impact | change | test | architecture
task: <the user's repository question>
scope_hint:
  project_id: <exact ID or null>
  file_path: <repository-relative path or null>
anchors:
  symbols: []
  paths: []
  errors: []
required_evidence: []
limits:
  max_actions: 6
  max_delegations: 0
```

Never turn an ambiguous project, symbol, or relationship into a repository
fact. Preserve uncertainty in the contract and final packet.

## First action and scope

When a live CodeNib MCP connection is available, inspect its live registration
and use `get_manifest` once for capability and provenance state. Resolve the
host's actual registered tool names; never invent or copy an assumed MCP
prefix.

`explore_context` is the first context-retrieval action for every non-trivial
task. Start with `budget="fast"`, pass every known `project_id` and
`file_path`, include known symbols, and inspect all of these before making a
claim:

- `scope.status` and `scope.complete`;
- `project_context`;
- `source.verified` and source identity;
- `relationships` and their direction/provider;
- `diagnostics` and delivery limits.

An explicit unresolved project scope is an abstention condition; do not widen
it silently to workspace scope.

## Closed action set

Choose only one of these actions at a time:

```text
RESOLVE_SCOPE       inspect manifest and explore scope fields
EXPLORE             call bounded explore_context
SEARCH              use one targeted search or source-navigation route
NAVIGATE_GRAPH      use bounded dependency or project-impact navigation
LOCATE_VALIDATION   locate tests/configuration read-only; do not run them
VERIFY              re-check a named claim against stronger evidence
STOP                return the supported context packet
ABSTAIN             return explicit unresolved diagnostics
```

Every action must state its purpose, scope, budget, expected evidence, and
reason for any available tool that is not used. Use at most six actions. Stop
after a bounded action adds no new anchor or supported claim.

Do not duplicate CodeNib's deterministic retrieval planner. The Skill chooses
the evidence question; CodeNib chooses BM25, dense, hybrid, graph, and fallback
routes.

## MCP routing policy

All of the following read-only tools are available on the `full` surface. Do
not call every tool blindly; choose the narrowest route that closes the named
evidence gap and record why the others were skipped.

| Tool | Use when | Required boundary |
|---|---|---|
| `get_manifest` | capability, scope, or provenance is uncertain | record loaded views and diagnostics |
| `explore_context` | default first exploration or composed evidence | preserve scope and inspect all returned sections |
| `search_context` | explicit ranked retrieval route is needed | keep its selected plan and source metadata |
| `search_semantic` | concept is known but identifiers are not | candidates require source/LSP verification |
| `search_bm25` | exact names, symbols, or error strings are known | candidates are not proof |
| `search_regex` | structural graph patterns, tests, decorators, or node types | use path/node filters and read source afterward |
| `search_zoekt` | comments, docs, config, generated-looking, or off-graph text | use file filters and read source afterward |
| `dependency_subgraph` | bounded callers, callees, blast radius, or project dependencies | specify direction, granularity, and traversal caps |
| `find_projects_using` | named shared symbol/module needs workspace consumers | require a resolved seed; never infer consumers from names |
| `lsp_definition` | a candidate location needs symbol binding | verify the returned location with source |
| `lsp_references` | static usages or declarations need confirmation | static references are not complete dynamic impact |
| `lsp_route` | endpoint, bridge, factory, or provider routes are unclear | bind returned anchors to scope and source |
| `read_source` | a search/LSP/graph location must become evidence | use repository-relative paths and bounded 1-based ranges |

Use `balanced` or `thorough` only for a named gap after the fast exploration.
Missing providers, stale indexes, and incomplete graph coverage are diagnostics,
not permission to guess.

## Evidence and stopping

Represent each load-bearing claim as:

```yaml
claim: <statement>
status: supported | contradicted | unresolved
authority: live_source | indexed_excerpt | graph_fact | manifest_fact
citations:
  - file: <repository-relative path>
    start_line: <1-based line or null>
    end_line: <1-based line or null>
    node_id: <graph node or null>
    edge_type: <graph edge or null>
scope_project_id: <ID or null>
source_verified: true | false
confidence: high | medium | low
```

Keep source, graph, manifest, and inference distinct. Semantic similarity is
not a dependency edge. Dynamic imports, reflection, generated code, and
unavailable providers remain unresolved.

Stop when the mode's required predicates are satisfied, when one bounded
follow-up produces no new evidence, or when the delivery/action budget is
exhausted. If required evidence is unavailable, return `abstain` or
`budget_exhausted` with diagnostics instead of a completeness claim.

Return only a compact packet with the contract, scope, claims, anchors,
actions, tool-coverage reasons, diagnostics, gaps, and final status. Do not
persist hidden reasoning or create a second ledger; Phase 3 may connect these
summaries to CodeNib's existing runtime ledger and trace.

# CodeNib MCP Routing Reference

Use the narrowest route that closes the current evidence gap. Do not call all
tools by default. Every packet records whether each logical tool was used,
skipped, deferred, or unavailable and why.

| Tool | Use when | Boundary |
|---|---|---|
| `get_manifest` | Starting a connection or checking capability/provenance | Record views, providers, source identity, and diagnostics. |
| `explore_context` | Default first exploration or composed evidence | Preserve project/file scope and inspect every returned section. |
| `search_context` | A deterministic ranked route must be inspected explicitly | Retain the selected route and source metadata. |
| `search_semantic` | The concept is known but identifiers are not | Treat results as candidates; verify with LSP/source. |
| `search_bm25` | Exact names, symbols, or error strings are known | Candidates are not proof. |
| `search_regex` | Structural patterns, tests, decorators, or node types matter | Follow graph candidates with source reads. |
| `search_zoekt` | Comments, docs, configuration, or off-graph text matters | Use file filters and verify matched ranges. |
| `dependency_subgraph` | Bounded callers, callees, blast radius, or project relationships matter | Specify direction, granularity, depth, and caps. |
| `find_projects_using` | A resolved shared symbol/module needs workspace consumers | Never infer consumers from names. |
| `lsp_definition` | A candidate needs binding to a definition | Verify returned locations with source. |
| `lsp_references` | Static usages or declarations need confirmation | Static references are not complete dynamic impact. |
| `lsp_route` | Endpoint, bridge, factory, provider, or route anchors are unclear | Bind results to scope and source. |
| `read_source` | A location must become source evidence | Use repository-relative paths and bounded 1-based ranges. |

Use `fast` for orientation. Use `balanced` or `thorough` only for a named gap
after the first exploration. Missing providers, stale indexes, and incomplete
graph coverage remain diagnostics.

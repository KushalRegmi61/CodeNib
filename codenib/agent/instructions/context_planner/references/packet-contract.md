# CodeNib Context Packet Contract

Every planner or subagent result is a bounded evidence packet. It contains no
hidden reasoning or raw transcript.

```yaml
packet_version: 1
agent: context-planner | scope-search | impact-navigator | evidence-auditor
status: complete | abstain | budget_exhausted | tool_unavailable
question: <one evidence question>
scope:
  project_id: <string|null>
  file_path: <string|null>
  source_identity: <mapping|null>
claims:
  - evidence_id: ev_<sha256-prefix>
    claim: <short statement>
    status: supported | contradicted | unresolved
    authority: live_source | indexed_excerpt | graph_fact | manifest_fact
    citations:
      - file: <repository-relative path>
        start_line: <1-based integer|null>
        end_line: <1-based integer|null>
        node_id: <string|null>
        edge_type: <string|null>
    source_verified: true | false
    confidence: high | medium | low
actions: []
tool_coverage:
  get_manifest: used | skipped:<reason> | deferred:budget | unavailable:<reason>
  explore_context: ...
  search_context: ...
  search_semantic: ...
  search_bm25: ...
  search_regex: ...
  search_zoekt: ...
  dependency_subgraph: ...
  find_projects_using: ...
  lsp_definition: ...
  lsp_references: ...
  lsp_route: ...
  read_source: ...
tool_calls: []
delegations: []
diagnostics: []
gaps: []
recommendation: stop | continue | abstain
budget:
  actions: 0
  tool_calls: 0
  delegations: 0
```

Evidence IDs are recomputed from canonical claim text, citations, authority,
and source identity. Caller-provided IDs are not trusted. When a live host
prefixes a registered MCP name, the actual name may appear only in an
ephemeral `tool_calls` trace event; packets and receipts use logical names.

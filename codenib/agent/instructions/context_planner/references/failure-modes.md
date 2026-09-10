# Context-Planner Failure Modes

- Ambiguous project or file scope: abstain; do not widen to workspace scope.
- Stale or mismatched source identity: report diagnostics; do not cite indexed
  text as live checkout source.
- Missing vector, graph, Zoekt, or LSP provider: use only the documented
  fallback and mark the reduced coverage.
- Unresolved dynamic, reflective, generated, or inferred dependency: retain it
  as unresolved; never create a guessed edge.
- Empty or zero-yield follow-up: stop rather than repeating the same route.
- Tool registration missing or denied: emit `tool_unavailable` and do not guess
  a host-specific MCP name.
- Budget exhaustion: return the partial packet with gaps and a
  `budget_exhausted` status.
- Modified or unmanaged installed asset: fail closed; never overwrite it.

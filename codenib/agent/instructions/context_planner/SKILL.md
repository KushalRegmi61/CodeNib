---
name: context-planner
description: >-
  Build source-grounded repository context before non-trivial coding-agent
  work: localize files and symbols, explain behavior, trace dependency impact,
  identify validation surfaces, and abstain on stale or ambiguous evidence.
  Use for multi-file, dependency, architecture, debugging, or change-planning
  tasks; skip trivial edits with known scope.
---

# CodeNib Context Planner

Use this Skill in the main coding-agent context before non-trivial repository
work. It plans evidence; it does not edit files, run builds/tests, install
packages, or mutate indexes. Delegate only a named evidence gap to one of the
three installed read-only agents: `scope-search`, `impact-navigator`, or
`evidence-auditor`.

The Skill is a decision layer above CodeNib. CodeNib owns deterministic
retrieval, graph relationships, source verification, provenance, and response
limits. The planner owns the evidence question, route choice, delegation, and
stopping decision.

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
  max_delegations: 2
  max_tool_calls: 12
```

Never turn an ambiguous project, symbol, or relationship into a repository
fact. Preserve uncertainty in the contract and final packet.

## First action and scope

When a live CodeNib MCP connection is available, inspect its live registration
and use `get_manifest` once for capability and provenance state. Resolve the
host's actual registered tool names; never invent or copy an assumed MCP
prefix. The logical names in this Skill are the CodeNib server names, not
persisted host-specific names.

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

All thirteen CodeNib tools are available on the `full` surface. Choose the
narrowest route that closes the named evidence gap; do not call every tool
blindly. Use the detailed decision table in
`references/mcp-routing.md`, and record a compact decision for every logical
tool in the returned packet.

## Evidence and stopping

Use the packet schema in `references/packet-contract.md` for every claim,
anchor, diagnostic, tool decision, and stop recommendation.

Keep source, graph, manifest, and inference distinct. Semantic similarity is
not a dependency edge. Dynamic imports, reflection, generated code, and
unavailable providers remain unresolved.

Stop when the mode's required predicates are satisfied, when one bounded
follow-up produces no new evidence, or when the delivery/action budget is
exhausted. If required evidence is unavailable, return `abstain` or
`budget_exhausted` with diagnostics instead of a completeness claim.

Return only a compact packet with the contract, scope, claims, anchors,
actions, tool-coverage reasons, diagnostics, gaps, and final status. Read
`references/failure-modes.md` when a provider, scope, source identity, or
registration failure occurs. Do not persist hidden reasoning or create a
second ledger; the existing CodeNib runtime ledger and trace are the only
integration seam.

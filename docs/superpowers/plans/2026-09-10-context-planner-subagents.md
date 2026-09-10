# Context Planner Skill and Subagents — Implementation Plan

This is the implementation companion to the decision roadmap in
`roadmap.md`. It turns the roadmap into the first vertical slice without
adding a new MCP endpoint, graph schema, storage protocol, or execution agent.

## Decision

Implement one main-thread `.claude/skills/context-planner/SKILL.md` first, then
three read-only subagents:

- `.claude/agents/scope-search.md`
- `.claude/agents/impact-navigator.md`
- `.claude/agents/evidence-auditor.md`

The planner makes the adaptive decision about the next bounded evidence action.
CodeNib remains authoritative for scope, retrieval, graph facts, source
provenance, diagnostics, and response limits.

## Existing APIs to reuse

1. Start with `explore_context`, whose implementation accepts `project_id` and
   `file_path`, resolves scope, composes retrieval/LSP/dependency/source
   evidence, and returns diagnostics in
   `codenib/mcp/tools/explore.py:422-719`.
2. Preserve explicit-scope failure behavior from
   `codenib/graph/project_queries.py:122-276`.
3. Use `dependency_subgraph` symbol/project granularity and
   `find_projects_using` from `codenib/mcp/server.py:420-473`.
4. Do not replace the deterministic retrieval route planner documented in
   `codenib/model/retrieval_planner.py:5-9` and implemented at lines 120-240.

## Skill behavior

The main Skill constructs a context contract, calls `explore_context` with
`budget="fast"`, then chooses one action from this closed set:

    RESOLVE_SCOPE, EXPLORE, SEARCH, NAVIGATE_GRAPH,
    LOCATE_VALIDATION, VERIFY, STOP, ABSTAIN

Each action must carry its purpose, scope, seed/paths, budget, and expected
evidence predicate. The Skill must preserve the returned `scope`, `source`,
`project_context`, `relationships`, and `diagnostics` fields in its compact
ledger. A subagent is delegated only for a named evidence gap.

## Subagent contracts

### scope-search

Receives the task, current scope diagnostics, known file/project hints, and the
missing localization predicate. Uses `explore_context` first, then targeted
search/read confirmation. Returns no more than a small set of source anchors,
candidate projects, unresolved ambiguities, and a stop recommendation.

### impact-navigator

Receives explicit graph seeds and one structural question. Uses bounded
`dependency_subgraph` or `find_projects_using`, then source-linked
`explore_context` verification. It reports direction, granularity, traversal
limits, cross-project edges, and unresolved dynamic edges. It never infers a
missing edge from names or imports that the graph did not report.

### evidence-auditor

Receives the current packet and required predicates. Verifies claims against
source/graph provenance and locates tests, fixtures, manifests, CI, and scripts
with read-only inspection. It never runs build/test commands or edits files.
It returns supported, contradicted, and unresolved claims plus a
continue/stop/abstain recommendation.

## Planner pseudocode

    contract = infer_context_contract(task)
    packet = explore_context(
        query=contract.task,
        symbols=contract.symbols,
        project_id=contract.project_id or "",
        file_path=contract.file_path or "",
        budget="fast",
        top_k=6,
        include_dependencies=True,
    )

    if contract.explicit_scope and not packet.scope.complete:
        return abstain(packet.diagnostics)

    ledger.add(packet)
    while ledger.actions < 6:
        action = llm_choose_from_closed_set(contract, ledger)
        if action == STOP:
            audit = evidence_auditor(ledger) if audit_required(ledger) else None
            if audit:
                ledger.add(audit)
            if predicates_satisfied(ledger):
                return compact_evidence_packet(ledger)
            action = VERIFY
        if action == ABSTAIN:
            return compact_evidence_packet(ledger, status="abstain")
        if action is a direct bounded MCP/read action:
            ledger.add(execute(action))
        else:
            ledger.add(delegate_one_or_two_read_only_agents(action, ledger))

    return compact_evidence_packet(ledger, status="budget_exhausted")

## Files and vertical slices

### Slice 1: main Skill

Create the Skill with the contract, first-call rule, closed action set,
delegation conditions, stop conditions, explicit-scope abstention, and the
no-edit/no-execution boundary. Validate it on five manually curated tasks
before adding subagents.

### Slice 2: subagent prompts

Add the three agent files with narrow descriptions, the complete read-only MCP
surface, role-specific tool-routing rules, packet schemas, output caps, and no
recursive delegation. Pass all paths, project IDs, seeds, diagnostics, and
questions explicitly because isolated subagents do not receive the parent
conversation automatically. Every intentionally unused tool must have a
not-applicable, provider-unavailable, redundant, or budget-deferred reason.

### Slice 3: agent integration

Register the Skill and subagents with the connected coding agent. Resolve the
actual MCP tool names from the live registration; do not copy a guessed
`mcp__...` prefix from the research draft. Keep edits, builds, tests, and
index mutation in the main agent.

### Slice 4: evaluation and pruning

Compare raw file-tool exploration, direct CodeNib MCP, main Skill only, and
Skill plus subagents. Keep a subagent only if it improves supported evidence,
task success, or false-scope/error reporting at an acceptable token and latency
cost.

## Required fixtures and edge cases

- unique file-owner scope;
- unresolved explicit project scope must block retrieval;
- ambiguous project names;
- incomplete workspace fallback;
- exact symbol needing no delegation;
- symbol callers followed by project-level dependents;
- dynamic/reflection/generated-code edges remain unresolved;
- missing graph provider with retrieval still available;
- stale source identity labels evidence as indexed, not live;
- no-new-evidence bounded stop;
- tests/configuration located without execution;
- over-cap subagent result rejected or compacted.

## Verification

For prompt-only changes, run deterministic frontmatter, tool-reference, packet
shape, and policy-fixture checks without billed model calls. If CodeNib runtime
files change, run focused MCP/search/dependency/bounds/session tests first, then
the required unit tier:

    uv run python -m pytest \
      -m "not slow and not integration and not integration_serial and not integration_serial_consumer" \
      -x --tb=short

Before making graph-completeness claims, run `codenib codegraph status` and
confirm the index matches the current checkout. Missing language toolchains or
stale schema artifacts are environment diagnostics, not successful graph
verification.

## Explicit non-goals

- no `identify_project` or separate workspace MCP tool;
- no LLM planner inside `codenib/mcp` for the first slice;
- no schema bump, C++ decoder work, graph database, or generic storage layer;
- no shell/build/test permissions for context subagents;
- no duplicate retrieval or graph implementation in Skill prose;
- no completeness claims from ambiguous or incomplete evidence.

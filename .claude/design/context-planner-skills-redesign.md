# CodeNib Context Planner Skills — Updated Design

Status: aligned with the 2026-09-10 context-planner roadmap
Scope: coding-agent context planning over CodeNib MCP; no new graph store,
storage protocol, or autonomous coding runtime.

## 1. Final design decision

The earlier design correctly identified the problem: the roadmap was treating
capabilities as separate agents and would therefore create an over-fragmented,
tool-happy planner.

The updated decision is more precise:

```text
One planner-facing Skill:
  context-planner

Three bounded read-only subagents:
  scope-search
  impact-navigator
  evidence-auditor

Internal planner stages, not separate skills:
  task contract
  deterministic context mapping
  budget/ledger/stop control
```

The LLM owns the adaptive decision: which bounded evidence action should happen
next, whether independent exploration is worth delegating, and whether the
required evidence is sufficient to stop. CodeNib owns the facts: scope,
retrieval, graph relationships, source provenance, diagnostics, and hard
delivery limits.

This preserves the strongest parts of the previous proposal without turning
every intermediate capability into a separate planner-facing Skill or
subagent. The underlying MCP tools remain fully callable through the routing
policy defined below.

## 2. What is actually required

| Capability | Final home | Required behavior |
|---|---|---|
| Task interpretation | Internal stage of `context-planner` | Convert the user request into mode, scope hints, anchors, required evidence, and stop predicates. Hypotheses are not repository facts. |
| Project/workspace resolution | CodeNib deterministic scope resolver | Use explicit project ID, file ownership, exact identity, or workspace fallback with diagnostics. No separate `identify_project` agent/tool. |
| Scope-limited search | `explore_context` first; `scope-search` only for a gap | Preserve project/file scope through retrieval and return source-linked anchors. |
| Dependency and call navigation | `impact-navigator` over CodeNib graph tools | One role handles symbol callers/callees and project-level dependents; no guessed dynamic edges. |
| Validation-surface discovery | Conditional branch of `evidence-auditor` | Locate tests, fixtures, CI, manifests, and scripts read-only. Do not run them. |
| Evidence verification | `evidence-auditor` | Classify claims as supported, contradicted, or unresolved with authority and citations. |
| Budget and stopping | Internal planner ledger plus CodeNib bounds | Stop on satisfied predicates, zero-yield expansion, provider failure, or budget exhaustion. |

CodeNib already provides most of the deterministic machinery. The composed
MCP path accepts project/file scope and combines retrieval, LSP routing, graph
expansion, source reads, and diagnostics in
`codenib/mcp/tools/explore.py:422-719`. Its public wrapper is read-only,
idempotent, structured, and bounded at `codenib/mcp/server.py:202-228`.
Project resolution is deterministic at
`codenib/graph/project_queries.py:122-276`, and retrieval route selection is
explicitly non-LLM at `codenib/model/retrieval_planner.py:5-9` and
`:120-240`.

The missing quality layer is therefore task interpretation, evidence
validation, abstention, and stopping—not another search implementation.

## 3. Target architecture

```text
User task
   |
   v
context-planner Skill, main agent context
   |
   +--> build ContextContract
   +--> call explore_context(budget="fast")
   +--> inspect scope, plan, source, relationships, diagnostics
   |
   +--> LLM chooses one bounded next action
   |       |
   |       +--> direct CodeNib MCP/read action
   |       +--> scope-search
   |       +--> impact-navigator
   |       +--> evidence-auditor
   |
   +--> merge EvidencePackets into ledger
   +--> stop, continue, or abstain
   |
   v
Verified ContextPacket -> main agent edit/build/test loop

CodeNib remains below the planner:
scope -> retrieval -> route -> graph -> source -> provenance -> bounds
```

The main Skill runs in the coding agent’s current context. Context isolation is
appropriate for verbose subagents, but the planner must retain the task,
constraints, previous evidence, and current edit/test state. Claude’s official
Skill and subagent model supports this separation: Skills can be reusable
instructions, while subagents are isolated workers with selected tools and
models ([Skills](https://code.claude.com/docs/en/slash-commands),
[Subagents](https://code.claude.com/docs/en/sub-agents)).

## 4. Contracts

### 4.1 ContextContract

The planner first creates a compact contract. It may contain uncertainty, but
must not turn an unknown symbol or ambiguous project into a fact.

```yaml
ContextContract:
  mode: locate | explain | impact | change | test | architecture
  task: string
  scope_hint:
    project_id: string|null
    file_path: string|null
  anchors:
    symbols: [string]
    paths: [string]
    errors: [string]
  required_evidence:
    - implementation
    - callers_or_dependents
    - cross_project_usage
    - tests_or_validation
  stop_conditions:
    - required evidence predicates are satisfied
    - no new evidence after one bounded follow-up
    - budget or response limit is reached
  limits:
    max_actions: 6
    max_delegations: 3
    max_parallel_delegations: 2
```

The planner does not select BM25 versus dense retrieval. CodeNib’s deterministic
`RetrievalPlanner` makes that backend decision.

### 4.2 EvidenceClaim

```yaml
EvidenceClaim:
  claim: string
  status: supported | contradicted | unresolved
  authority: live_source | indexed_excerpt | graph_fact | manifest_fact
  citations:
    - file: string
      start_line: integer|null
      end_line: integer|null
      node_id: string|null
      edge_type: string|null
  scope_project_id: string|null
  source_verified: boolean
  confidence: high | medium | low
```

The following distinctions are mandatory:

- indexed locations are not live source when `source.verified` is false;
- manifest dependencies are not source imports;
- cross-project edges are explicitly labeled;
- directly observed relationships are separate from inferred hypotheses;
- ambiguous results are unresolved, never guessed;
- every supported claim has a source range, graph fact, manifest fact, or
  diagnostic.

### 4.3 EvidencePacket

```yaml
EvidencePacket:
  agent: scope-search | impact-navigator | evidence-auditor
  question: string
  scope:
    status: string
    project_ids: [string]
    complete: boolean
  claims: [EvidenceClaim]
  anchors:
    - file: string
      start_line: integer
      end_line: integer
      symbol: string|null
      reason: string
  gaps: [string]
  diagnostics: [string]
  actions_taken: [string]
  provider_summary: [string]
  stop_recommendation: continue | stop | abstain
```

Packets are compact. They do not contain raw subagent transcripts, hidden
reasoning, whole source files, edits, command output, or nested delegation.

## 5. Planner-visible actions

The LLM may select only these actions:

```text
RESOLVE_SCOPE       inspect scope from explore_context
EXPLORE             call bounded explore_context
SEARCH              call search_context or targeted read-only search
NAVIGATE_GRAPH      call dependency_subgraph/find_projects_using
LOCATE_VALIDATION   inspect tests/configuration read-only
VERIFY              audit claims or delegate an evidence check
STOP                return the current supported packet
ABSTAIN             return explicit unresolved diagnostics
```

There is no free-form generated tool plan, arbitrary shell loop, or repeated
search after a zero-yield action. The LLM chooses among bounded actions; the
tools and CodeNib implementation enforce the actual limits.

## 6. The three subagents

### 6.1 `scope-search`

Purpose: localize an implementation, symbol, error, or behavior when the first
fast exploration is weak, ambiguous, or scope-incomplete.

The subagent receives the complete read-only CodeNib MCP surface. Its routing
policy is to use `explore_context` first, preserving `project_id` and
`file_path`, then select the lowest-cost search, LSP, graph, or source tool
that closes the named gap. It can use `search_context`, `search_semantic`,
`search_bm25`, `search_regex`, `search_zoekt`, `lsp_definition`,
`lsp_references`, `lsp_route`, `dependency_subgraph`,
`find_projects_using`, `read_source`, and `get_manifest` when their decision
rules below apply. It returns a small set of source anchors, candidate scope,
tried terms, capability/fallback information, and unresolved ambiguity.

It is a cheap, bounded breadth role. It does not perform multi-hop graph
analysis, edit files, run shell commands, or delegate further.

### 6.2 `impact-navigator`

Purpose: navigate symbol and project impact from an explicit graph seed.

The subagent receives the complete read-only CodeNib MCP surface, not only the
graph tools. It uses `get_manifest` to confirm graph/LSP availability,
`dependency_subgraph` with explicit direction, granularity, and bounds, and
`find_projects_using` for named shared symbols/modules. It may use
`search_context`, all direct search variants, `lsp_definition`,
`lsp_references`, and `lsp_route` to bind a fuzzy seed before traversal, and
`explore_context` or `read_source` to verify important graph results against
source. It reports seed identity, traversal statistics, cross-project edges,
provider fallbacks, and unresolved dynamic/reflection/generated-code
relationships.

This intentionally merges call-graph expansion and dependency-impact. The
current API already exposes symbol/project graph granularity at
`codenib/mcp/server.py:420-453` and project usage at
`:456-473`; two agents would duplicate context and provenance handling.

It must never infer a missing edge from naming similarity or semantic search.

### 6.3 `evidence-auditor`

Purpose: determine whether the current packet supports the task and whether
important validation evidence is missing.

The subagent receives the complete read-only CodeNib MCP surface. It may verify
claims with `explore_context`, `search_context`, any direct search variant,
`read_source`, `lsp_definition`, `lsp_references`, `lsp_route`,
`dependency_subgraph`, or `find_projects_using`; it uses `get_manifest` to
explain unavailable providers. It may inspect test files, fixtures, manifests,
CI configuration, and scripts with Read/Grep/Glob. It classifies each important
claim and returns gaps plus a stop/continue/abstain recommendation.

It has no Edit, Write, Bash, build, test, install, or index-mutation access.
The main coding-agent thread owns execution and mutation.

## 7. Planner loop

```python
contract = infer_context_contract(user_task)
ledger = EvidenceLedger(contract)

first = explore_context(
    query=contract.task,
    symbols=contract.anchors.symbols,
    project_id=contract.scope_hint.project_id or "",
    file_path=contract.scope_hint.file_path or "",
    budget="fast",
    top_k=6,
    include_dependencies=True,
)
ledger.add(first)

if explicit_scope_was_requested(contract) and not first["scope"]["complete"]:
    return abstain(first["diagnostics"])

for _ in range(contract.limits.max_actions):
    action = llm_choose_from_closed_set(contract, ledger.compact_view())

    if action == "STOP":
        if audit_required(contract, ledger):
            ledger.add(delegate("evidence-auditor", ledger))
        if required_predicates_satisfied(contract, ledger):
            return ledger.packet()
        action = "VERIFY"

    if action == "ABSTAIN":
        return ledger.packet(status="abstain")

    if action is a direct bounded MCP/read action:
        ledger.add(execute(action))
    else:
        ledger.add(delegate_one_or_two_independent_agents(action, ledger))

return ledger.packet(status="budget_exhausted")
```

Delegation is justified only by an explicit evidence gap, verbose exploration,
independent local-search and impact questions, or a high-risk claim requiring a
second read-only check. The main planner owns delegation and subagents cannot
spawn more agents.

## 8. Quality gates

The planner must be able to return “not enough evidence.” Required gates are:

- explicit project scope never silently falls back to workspace scope;
- no final claim without provenance;
- unverified indexed content is not presented as live source;
- semantic similarity is not treated as a dependency edge;
- impact claims include bounded caller/dependent evidence;
- “tests found” requires actual test/configuration evidence;
- repeated zero-yield expansion causes a stop;
- source, manifest, graph, and inferred relationships remain distinct;
- incomplete provider/index state blocks completeness claims;
- missing graph tooling becomes a diagnostic, not a guessed result.

The goal is useful, supported context—not maximum retrieved bytes. This aligns
with evidence that agents often explore context without using it effectively,
and that no single retrieval family dominates across tasks ([ContextBench](https://arxiv.org/abs/2602.05892),
[Agent Retrieval Bench](https://arxiv.org/abs/2607.24882)).

## 9. Existing skill migration

Keep low-level skills for direct use and benchmarking, and expose the complete
registered MCP tool surface to the context planner and all read-only subagents.
Do not invoke every tool on every task; use the routing matrix and record why a
tool was not needed:

- BM25, embedding, hybrid retrieval, and rerankers remain retrieval operators;
- callers, callees, trace, LSP definition/references/route remain navigation
  operators;
- `codenib_context` and `repository_search` should converge on the canonical
  `explore_context` path rather than become competing planners;
- `code_to_query` belongs in task-contract/query normalization if it remains;
- test locating is a conditional evidence-auditor branch, not a mandatory
  planner step;
- the existing context ledger, bounds, source fingerprints, and session
  deduplication remain infrastructure.

Do not remove compatibility skills in the first slice. First measure whether
the planner actually replaces or improves their use; then alias or retire
duplicates in a separate cleanup change.

## 10. Implementation order

### Phase 1 — main Skill baseline

Create `.claude/skills/context-planner/SKILL.md` with the contract, closed
actions, first `explore_context` call, explicit-scope abstention, stopping
logic, and no-edit/no-execution rules. Validate it on localization,
explanation, impact, cross-project change, and test-surface tasks without
subagents.

### Phase 2 — three read-only agents

Create:

- `.claude/agents/scope-search.md`;
- `.claude/agents/impact-navigator.md`;
- `.claude/agents/evidence-auditor.md`.

Give each a narrow description, the complete read-only MCP tool surface, a
role-specific routing policy, packet format, output cap, and no recursive
delegation. Pass paths, project IDs, graph seeds, diagnostics, and the exact
question explicitly because isolated agents do not inherit the parent
conversation automatically. Full availability does not mean that every agent
must call every tool: each unused tool must be explained as not applicable,
provider-unavailable, redundant after a stronger result, or deferred by budget.

### Phase 3 — integration and traceability

Register the Skill and agents with the connected coding agent. Resolve actual
MCP tool names from the live registration rather than copying an assumed
prefix. Where an existing runtime seam is appropriate, record planner actions
and evidence IDs through the current agent ledger/trace infrastructure; do not
create a second persistence system.

### Phase 4 — ablation and pruning

Compare:

1. raw file-tool coding agent;
2. direct CodeNib MCP;
3. main Skill without subagents;
4. main Skill with the three subagents.

Measure task success, localization precision/recall, scope accuracy, supported
claim rate, unsupported claim rate, unresolved-edge reporting, useful-context
ratio, bytes, tokens, latency, tool calls, subagent calls, edit churn, and
abstention quality.

Keep a subagent only if it provides measurable value over the main Skill and
direct MCP. Multi-agent breadth is expensive and is not automatically suitable
for tightly coupled coding tasks, so parallelism remains conditional
([Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 11. Explicit boundaries

- no separate `identify_project` or workspace MCP tool;
- no LLM planner inside `codenib/mcp` for v1;
- no graph schema bump, C++ decoder change, graph database, or generic storage;
- no shell/build/test execution from context subagents;
- no duplicate retrieval or graph implementation in Skill prose;
- no raw chain-of-thought persistence;
- no completeness claim from ambiguous, stale, or incomplete evidence.

This updated design supersedes the earlier recommendation of only two LLM
subagents (`task_contract` and `evidence_auditor`). Task-contract and
context-mapping remain conceptual planner stages, while `scope-search` and
`impact-navigator` are the bounded workers needed when the main planner has a
real localization or structural-evidence gap.

## 12. Complete MCP surface registration and routing

The planner and all three subagents must connect to the MCP server with the
`full` tool surface. The `explore` surface intentionally exposes only
`explore_context`; it is useful as a benchmark arm, but it cannot satisfy the
full context-planning design. This boundary is implemented by
`codenib/mcp/tool_surface.py:16-80` and documented in `docs/mcp.md:100-113`.

The connected registration must expose these thirteen tools from
`codenib/mcp/server.py:202-600`:

```text
explore_context       search_context       search_semantic
search_bm25           search_regex         search_zoekt
dependency_subgraph   find_projects_using  lsp_definition
lsp_references        lsp_route            read_source
get_manifest
```

`codenib-guide` is an MCP prompt resource, not a tool. When the host supports
MCP prompt retrieval, the main Skill should load it once at startup and pass the
relevant routing guidance to subagents. The prompt describes the same tool
selection rules in `codenib/mcp/prompts.py:44-149`; it does not replace direct
tool registration.

### 12.1 Registration invariant

At integration time:

1. Register the Skill and each subagent with the connected coding agent.
2. Discover the actual MCP server/tool names through live registration or
   `list_tools`; never copy an assumed `mcp__...` prefix into prompts.
3. Confirm the server reports `tool_surface: full` through `get_manifest` and
   that all thirteen names are visible.
4. Store the discovered names and capability snapshot in the current planner
   ledger for the session.
5. If a tool is missing, record a `tool_unavailable` diagnostic and use the
   documented fallback. Do not silently pretend that the full surface exists.

The planner and subagents have full read-only MCP access. Their prompts provide
role-specific routing instructions, not separate restrictive MCP allowlists.
The host may still deny a tool; that denial is an explicit capability failure.
No MCP tool in the current server edits files or executes builds/tests.

### 12.2 Tool routing matrix

| Tool | Use when | Required inputs / follow-up | Fallback and boundary |
|---|---|---|---|
| `get_manifest` | Start of each MCP connection; capability or provenance is uncertain | Record loaded views, view errors, workspace/project retrieval, LSP provider, source verification, and tool surface | No silent fallback. Provider state becomes a diagnostic |
| `explore_context` | Default first exploration and any composed evidence request | Pass query, known symbols, `project_id`/`file_path`, budget, direction, dependency/test flags; inspect scope, plan, source, relationships, diagnostics | Decompose into lower-level tools only when a named gap or missing provider requires it |
| `search_context` | Need ranked search with an explicit deterministic route and project filter, without necessarily requesting composed source windows | Pass query, budget, level, `filter_test`, and `project_id`; retain returned plan/source metadata | Use `search_bm25`, `search_semantic`, or `dependency_subgraph` only for the specific route that must be forced |
| `search_semantic` | Conceptual behavior, natural-language description, or semantic gap with no reliable exact identifier | Pass query, level, threshold, and `project_id` when known; verify candidate locations with LSP/source | If vector is unavailable, use `search_context`, then BM25/Zoekt and label the reduced route |
| `search_bm25` | Exact symbol, class, function, error string, or identifier lookup | Pass query, `top_k`, `filter_test`, and `project_id`; use results as candidates, not final claims | Use `search_regex` for structural patterns or `search_zoekt` for raw/off-graph text |
| `search_regex` | Structural pattern over CodeGraph file/symbol nodes, test naming, decorators, TODOs, or node-type constrained search | Pass regex, `file_glob`, `node_type`, case mode; derive a project glob from resolved path because the tool has no `project_id` parameter | If symbol graph is unavailable, use Zoekt for raw text or BM25/search_context; do not claim graph coverage |
| `search_zoekt` | Raw text in comments, docs, configuration, generated-looking text, or files not represented in CodeGraph | Pass query and `file_filter`; derive a project file filter when scope is known | If Zoekt is unavailable, use regex/BM25/search_context and report that off-graph coverage was unavailable |
| `dependency_subgraph` | Structural callers, callees, bounded blast radius, or project dependency rollup | Pass resolved symbol, direction, depth, node/edge caps, and symbol/project granularity | Bind fuzzy names with BM25/LSP first; if graph is unavailable, use LSP references plus text evidence and mark impact incomplete |
| `find_projects_using` | A named shared symbol/module must be checked across workspace projects | Pass a resolved symbol and project/evidence caps; follow returned project IDs with scoped search | If project graph is unavailable, report cross-project usage unresolved; never infer it from name matches |
| `lsp_definition` | A candidate symbol/location needs binding to its definition | Pass symbol or repository-relative file plus 1-based line/character; then call `read_source` | Use `lsp_route` or search candidates; indexed fallback must retain provider metadata |
| `lsp_references` | Need static usages, declarations, or a caller/reference cross-check | Pass symbol or file position, declaration flag, and cap; then inspect selected locations | Use `dependency_subgraph` for transitive impact, or search tools for textual fallback; static references are not complete dynamic impact |
| `lsp_route` | Need compact endpoint/bridge/factory/provider/value/type route anchors, or no reliable symbol is known | Pass symbol seeds or a non-blank query, neighbor flag, and cap; then bind/read selected anchors | Direct `lsp_route` has no project filter; use scoped `explore_context` when project isolation is load-bearing, and record any post-filter limitation |
| `read_source` | A location from search/LSP/graph must become source-backed evidence | Pass repository-relative POSIX path and 1-based inclusive range; keep within the 200-line/16,000-character bound | If source is unverified, retain indexed excerpt as `indexed_excerpt`; never label it live source |

Every tool has a deliberate owner but is available to every read-only planner
participant:

- `context-planner`: may use any tool directly when choosing the next action;
- `scope-search`: owns localization but may use every search, LSP, source, and
  manifest route when its primary route is unavailable or ambiguous;
- `impact-navigator`: owns structural navigation but may use every search, LSP,
  source, and manifest route to bind and verify graph seeds;
- `evidence-auditor`: owns verification but may use every route to falsify or
  support a claim, including raw search for tests/configuration and manifest
  capability checks.

### 12.3 Tool selection rules by question

The agents should apply these decision paths rather than learning isolated
tool names:

```text
Every new MCP connection:
  get_manifest
  -> verify full surface, loaded views, source verification, provider errors

Unknown feature / bug:
  explore_context(budget="fast", project_id/file_path when known)
  -> if weak: search_context
  -> if conceptual gap: search_semantic
  -> if exact identifier appears: search_bm25
  -> if pattern/config/test naming is needed: search_regex or search_zoekt
  -> lsp_definition/lsp_route to bind candidates
  -> read_source for final implementation evidence

Known symbol:
  search_bm25 or lsp_definition
  -> read_source definition
  -> lsp_references for direct usages
  -> dependency_subgraph for bounded callers/callees
  -> read_source selected impact anchors

Change-impact / cross-project task:
  get_manifest and scoped explore_context
  -> lsp_definition or search_bm25 to resolve the seed
  -> dependency_subgraph(granularity="symbol", direction="impact")
  -> find_projects_using for external consumers
  -> dependency_subgraph(granularity="project")
  -> scoped search_context per relevant project
  -> read_source and evidence audit

Tests, CI, docs, comments, or configuration:
  explore_context(filter_test=true) when source/index context is sufficient
  -> search_regex for CodeGraph test/pattern structure
  -> search_zoekt for raw/off-graph text
  -> search_bm25 for named test/config symbols
  -> read_source exact files
  -> report located surfaces without executing them

Route/flow explanation:
  lsp_route(symbols=[...]) or lsp_route(query=...)
  -> lsp_definition for binding
  -> lsp_references for usage edges
  -> dependency_subgraph for transitive structural edges
  -> read_source at each explanatory anchor
```

These are default routes, not mandatory fixed chains. The LLM may stop early
when the required evidence predicate is satisfied, or choose a different tool
when `get_manifest` or a prior diagnostic proves the preferred provider is
unavailable.

### 12.4 Capability-aware fallback protocol

Before selecting a provider-specific tool, inspect `get_manifest` and prior
diagnostics:

- no vector view: do not repeatedly call `search_semantic`; use ranked or
  lexical alternatives and mark semantic coverage unavailable;
- no BM25 view: use semantic, regex, or Zoekt according to the question;
- no symbol graph: do not call graph/LSP tools expecting completeness; use raw
  search/source and mark structural relationships unresolved;
- no Zoekt view: use CodeGraph regex or ranked search and state that comments,
  docs, and off-graph text may be missed;
- unverified source: do not call `read_source` as if it were authoritative;
  retain indexed evidence with its fingerprint/provider status;
- incomplete workspace/project retrieval: do not claim complete cross-project
  coverage;
- `explore` tool surface: treat all lower-level tools as unavailable and do
  not report full-surface coverage.

### 12.5 Coverage and traceability gate

“Use all tools” means **all tools are registered, understood, and reachable
when appropriate**, not that every task should fan out to all thirteen. To
prevent accidental under-utilization:

1. Maintain one fixture for each tool’s primary use and provider-unavailable
   fallback.
2. Evaluate every planner/subagent role against the complete tool matrix.
3. Record every attempted and intentionally skipped tool with a reason:
   `not_applicable`, `provider_unavailable`, `redundant_after_evidence`, or
   `budget_deferred`.
4. Treat a tool with repeated `not_applicable` outcomes across its intended
   fixture as a routing defect, not as acceptable non-use.
5. Track tool coverage, route correctness, evidence yield, duplicate bytes,
   latency, and unsupported-claim rate together. Raw call count is not a
   quality metric.

Use the existing context ledger and agent trace for this record. A call record
may contain:

```yaml
ToolUseRecord:
  agent: context-planner | scope-search | impact-navigator | evidence-auditor
  tool: string
  reason: string
  scope_project_id: string|null
  provider: string|null
  outcome: success | empty | unavailable | error | skipped
  skip_reason: string|null
  evidence_ids: [string]
  fallback_of: string|null
```

This is trace metadata, not a new database or persistence protocol. Reuse the
existing agent ledger/trace seam in `codenib/agent/runtime/context.py` and
`codenib/agent/runtime/trace.py`; do not create a second planner store.

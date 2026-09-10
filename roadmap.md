# CodeNib Context Planner Roadmap

Status: decision-ready roadmap
Date: 2026-09-10
Scope: the context-planning layer used by a coding agent, not a new MCP
database, graph store, or autonomous coding runtime.

## 1. Decision

Adopt a hybrid of the two proposals:

1. Use Claude/Coding-Agent deployment primitives: one main-thread
   context-planner Skill and a small number of isolated, read-only subagents.
2. Use CodeNib as the deterministic evidence engine: scope resolution, ranked
   retrieval, LSP routing, graph traversal, source reads, provenance,
   diagnostics, response bounds, and session accounting stay in CodeNib.
3. Give the LLM genuine decision authority over the next bounded action and the
   stopping decision. Do not give it authority to manufacture project identity,
   dependency edges, source claims, or completeness.
4. Return compact, evidence-bearing packets from subagents. The main coding
   agent receives decisions and citations, not raw exploration transcripts.

The winning design is:

    coding task
        |
        v
    main coding-agent thread
        |
        +--> context-planner Skill
        |       |
        |       +--> cheap direct explore_context/search/graph calls
        |       +--> bounded read-only subagents when breadth is worthwhile
        |       +--> evidence audit and explicit stop/abstain decision
        |
        +--> edit / build / test loop remains in the main agent

    CodeNib MCP: deterministic scope -> retrieval -> route -> graph -> source
                 -> provenance/diagnostics -> byte/session bounds

This keeps the useful part of the Claude design—fresh context for verbose
parallel exploration—while adding the missing evidence contract, authoritative
verification, and strict abstention path. It also preserves the existing
CodeNib runtime decision that compiled context assembly should not be replaced
by a menu of low-adoption skills.

## 2. Verdict on the two proposals

| Decision area | Claude proposal | Earlier minimal-subagent proposal | Final decision |
|---|---|---|---|
| Deployment shape | Main Skill plus isolated subagents | Few specialized roles | Keep both: one main Skill, three roles |
| Project discovery | Separate identify_project skill/tool | Deterministic scope resolution | Reject separate tool. Current MCP has no identify_project; explore_context already resolves scope |
| Search | Scope-limited search agent | Deterministic retrieval | Keep a cheap scope-search agent, with CodeNib retrieval authoritative |
| Graph | Separate call-graph and dependency-impact agents | One impact/evidence role | Merge into impact-navigator; current APIs expose symbol/project graph modes |
| Tests/build | Test-locator with shell access | Evidence auditor | Use read-only evidence-auditor; locate tests/configuration, never execute commands |
| Planner | LLM or rules | LLM chooses bounded roles | LLM chooses next action and stop; deterministic tools enforce facts and limits |
| Output | Natural-language summaries | Evidence packets | Require compact packets with supported/contradicted/unresolved claims |
| MCP changes | Several new endpoints | Reuse current seams | No new MCP endpoint in the first slice |
| Persistence | Extended graph/schema implied by older roadmap | Existing persisted graph | No schema or storage change for planner v1 |

The Claude research has one concrete repository error: it describes an existing
identify_project MCP tool. The current server exposes explore_context,
search_context, semantic/BM25 search, graph traversal, find_projects_using, LSP
routing, and source reading, but not that tool. This roadmap does not implement
an API merely because it appeared in the draft.

The Claude artifact also calls the roster “three subagents” while listing four:
scope-search, call-graph-expansion, dependency-impact, and test-locator. The
final roster deliberately has three by merging the two graph roles and
replacing shell-based test discovery with evidence auditing.

## 3. Current repository foundation

The planner is not starting from an empty retrieval layer.

### 3.1 One bounded context call already exists

codenib/mcp/tools/explore.py:422-487 accepts query, symbols, budget, direction,
project ID, and file path. It resolves scope before retrieval and blocks
retrieval when an explicitly requested project cannot be resolved.

The same implementation composes:

- ranked retrieval at codenib/mcp/tools/explore.py:479-500;
- LSP-shaped routing at codenib/mcp/tools/explore.py:501-526;
- bounded dependency expansion at codenib/mcp/tools/explore.py:542-600;
- verified live-source reads or indexed fallback at
  codenib/mcp/tools/explore.py:602-641;
- project context, plan, source identity, diagnostics, and delivery metadata
  at codenib/mcp/tools/explore.py:643-719.

The public MCP wrapper marks this tool read-only, idempotent, structured, and
bounded in codenib/mcp/server.py:202-228. It is the default first action for
the planner, not one skill among many that the model may forget to use.

### 3.2 Scope is deterministic and already carries failure states

codenib/graph/project_queries.py:122-276 resolves scope by explicit project ID,
canonical file ownership, exact project identity, and finally workspace
fallback. It represents unavailable, ambiguous, unresolved, and incomplete
states with diagnostics. The planner must consume these states directly:

- an explicit unresolved project_id is a hard stop;
- an ambiguous inferred scope requires clarification or a deliberately
  workspace-wide bounded search;
- workspace fallback is not evidence that every project is relevant;
- scope.complete == false prevents a completeness claim.

### 3.3 Retrieval already has a deterministic backend planner

codenib/model/retrieval_planner.py:5-9 explicitly defines its planner as
non-LLM path selection. RetrievalPlanner and its budget tiers are implemented
at codenib/model/retrieval_planner.py:120-240. This is the backend route
planner and should remain so. The proposed LLM context-planner is a
higher-level policy over evidence gaps; it must not duplicate this planner.

### 3.4 Structural navigation is already project-aware

The MCP dependency_subgraph wrapper exposes symbol and project granularity in
codenib/mcp/server.py:420-453, and find_projects_using is exposed at
codenib/mcp/server.py:456-473. The planner should use these routes before any
graph-schema work is considered.

### 3.5 Existing skills are components, not the planner roster

The loader and skill runtime already support typed retrieval, expansion, and
custom skills under codenib/agent/skills/loader.py:31-41 and
codenib/agent/skills/core.py. The current catalog contains overlapping
low-level skills, including codenib_context, repository search, callers,
callees, trace, and LSP routes. They can remain available for compatibility and
benchmarks, but the context planner should expose only its closed action set.

The older codenib_context composer remains useful as an internal retrieval
component in codenib/agent/skills/codenib_context/executor.py:1-40, but it must
not become a second planner with a competing scope/provenance contract.

## 4. Target contracts

The first implementation may encode these contracts in Skill and subagent
instructions. If evaluation shows that parsing is fragile, promote them to
typed models under a planner-specific module; do not introduce a generic
orchestration framework prematurely.

### 4.1 Context contract

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
      constraints:
        max_actions: 6
        max_delegations: 3
        max_parallel_delegations: 2

The planner may infer mode and missing requirements, but it must record
uncertainty instead of silently converting an ambiguous task into a precise
scope.

### 4.2 Evidence claim

Every load-bearing statement returned to the main agent must be representable
as:

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

“The graph found a caller” is not enough. The packet must identify the node or
edge and whether the source view is live and verified. An LLM explanation
without a citation is a hypothesis, not an evidence claim.

### 4.3 Subagent result packet

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

Packets are bounded and compact. A subagent must not paste an entire source
file, reproduce hidden reasoning, spawn another subagent, edit files, or run
build/test commands.

## 5. Minimal high-quality subagent roster

The planner has one Skill and three specialized subagents. These are roles,
not three independent autonomous agents trying to solve the coding task.

### 5.1 scope-search

Purpose: cheaply localize the feature, symbol, error, or behavior and return
the smallest useful set of source anchors.

Use when the first explore_context(..., budget="fast") call is weak, semantic,
lexical, or scope-incomplete; or when the task has multiple possible entry
points.

Full read-only CodeNib MCP surface plus Read, Grep, and Glob is available. Use
explore_context first and preserve project_id/file_path; then select the search,
LSP, graph, source, or manifest tool that closes the named gap. The role prompt
defines when to use each tool; it must not call all tools blindly.

Must return: top anchors, candidate scope, exact unresolved ambiguity, search
terms tried, and whether the result is sufficient to hand to graph or evidence
work.

Limits: cheap model, at most one bounded exploration branch, no graph traversal
beyond the default composed call, no edits, no shell.

### 5.2 impact-navigator

Purpose: answer structural questions: callers, callees, implementations,
dependents, cross-project use, and bounded blast radius.

Use when the task asks who uses this, what breaks if this changes, what calls
this, or the implementation anchor is known but its impact is not.

Full read-only CodeNib MCP surface plus Read, Grep, and Glob is available.
Prefer get_manifest, dependency_subgraph, and find_projects_using for structural
questions, then use search, LSP, explore_context, and read_source to bind and
verify seeds. Apply explicit direction, granularity, and depth/node/edge limits.

Design: this merges Claude’s call-graph-expansion and dependency-impact. The
distinction is an action parameter, not a reason for two isolated agents. One
agent can switch from symbol-level callers to project-level aggregation while
retaining the same scope and provenance contract.

Must return: seed identity, direction, granularity, bounded traversal
statistics, cross-project edges, supported impact claims, and unresolved
dynamic/reflection edges. It must never infer a missing edge from naming alone.

Limits: one graph question per delegation; bounded depth and node/edge counts;
no recursive delegation; no edits or shell commands.

### 5.3 evidence-auditor

Purpose: audit whether the proposed context supports the task and locate
validation surfaces such as tests, fixtures, CI configuration, and project
scripts without executing them.

Use when the planner is about to stop, the task requires a change plan, the
graph has dynamic/unresolved edges, multiple candidates conflict, or test
coverage/build ownership is part of the request.

Full read-only CodeNib MCP surface plus Read, Grep, and Glob is available. Use
get_manifest and any search, LSP, graph, explore, or source route needed to
verify a claim. It may inspect manifests, CI files, test directories, and
scripts as evidence.

Must return: supported, contradicted, and unresolved claims; missing evidence
predicates; source/graph provenance mismatches; relevant test/configuration
anchors; and a stop/continue/abstain recommendation.

Limits: no Edit, Write, Bash, build, test, install, or generated-file commands.
The main coding-agent thread owns mutation and execution.

### 5.4 Deliberately deferred roles

- No separate project-discovery agent: deterministic scope resolution is
  already part of explore_context.
- No separate test-locator agent in v1: useful read-only behavior belongs in
  evidence-auditor; a shell-capable test agent would blur planning with
  execution.
- No separate generic retrieval agent: CodeNib’s retrieval planner is already
  deterministic and should not be reimplemented in prose.
- No subagent receives an Agent/delegation tool. The main planner owns the
  delegation budget and prevents recursive fan-out.

## 6. Main context-planner Skill

Create .claude/skills/context-planner/SKILL.md as an automatically
discoverable Skill for multi-file, cross-project, architecture, impact,
debugging, and change-planning tasks. It runs in the main context, not a fork:
the planner must see the current task, user constraints, previous tool results,
and the main agent’s edit/test state.

Suggested frontmatter:

    ---
    name: context-planner
    description: >-
      Plan repository context before non-trivial coding-agent changes. Use when
      a task spans files, symbols, projects, dependencies, tests, architecture,
      or uncertain localization. Return bounded, source-linked evidence and
      stop when the required context predicates are satisfied.
    ---

The Skill must define a closed action set:

    RESOLVE_SCOPE       use explore_context fast scope fields
    EXPLORE             use bounded explore_context
    SEARCH              use search_context or targeted read-only search
    NAVIGATE_GRAPH      use dependency_subgraph/find_projects_using
    LOCATE_VALIDATION   inspect tests/configuration read-only
    VERIFY              delegate evidence-auditor or verify claims directly
    STOP                return an evidence-backed context packet
    ABSTAIN             state the unresolved scope/data limitation

The LLM chooses among these actions based on the current evidence ledger. It
does not choose arbitrary Python, shell, database, or graph operations.

### 6.1 Planner control loop

    contract = infer_context_contract(user_task)
    ledger = EvidenceLedger(contract)

    first = explore_context(
        query=contract.task,
        symbols=contract.anchors.symbols,
        file_path=contract.scope_hint.file_path or "",
        project_id=contract.scope_hint.project_id or "",
        budget="fast",
        top_k=6,
        include_dependencies=True,
    )
    ledger.add(first)

    if explicit_scope_was_requested(contract) and not first["scope"]["complete"]:
        return ABSTAIN("explicit scope unresolved", diagnostics=first["diagnostics"])

    for action_number in range(contract.constraints.max_actions):
        decision = llm_choose_next_action(
            contract=contract,
            evidence=ledger.compact_view(),
            allowed_actions=closed_action_set,
            remaining_budget=ledger.remaining_budget(),
        )

        if decision.action == "STOP":
            audit = run_evidence_auditor_if_required(ledger, contract)
            ledger.add(audit)
            if ledger.required_predicates_satisfied():
                return ledger.final_packet()
            decision = "VERIFY"

        if decision.action == "ABSTAIN":
            return ledger.final_packet(status="abstain")

        if decision.action in {
            "EXPLORE", "SEARCH", "NAVIGATE_GRAPH", "LOCATE_VALIDATION"
        }:
            result = execute_one_bounded_action(decision, contract)
            ledger.add(result)
            continue

        if decision.action == "VERIFY":
            packet = delegate_one_or_two_independent_read_only_agents(
                decision, contract, ledger
            )
            ledger.add(packet)
            continue

    return ledger.final_packet(status="budget_exhausted")

The pseudocode is policy-oriented. CodeNib continues to enforce its own actual
limits; the Skill must not pretend that a prompt-level token counter is
authoritative.

### 6.2 Delegation policy

Delegate only when at least one of these is true:

- the task has independent local-search and impact questions;
- exploration is verbose enough that it would pollute the main context;
- the main ledger has a concrete unresolved evidence predicate;
- a second independent read-only view can falsify a high-risk claim.

Do not delegate a single obvious symbol lookup. Run at most two subagents in
parallel, only when their questions are independent. The main planner merges
packets by claim identity and rejects unsupported conflicts rather than voting
between prose answers.

### 6.3 Stop conditions

Stop when every required predicate is satisfied, for example:

- locate: one or more high-confidence implementation anchors;
- explain: implementation plus relevant call/data flow and source evidence;
- impact: bounded callers/dependents plus explicit unresolved dynamic edges;
- change: implementation, impact, tests/configuration, and scope completeness;
- test: test files/configuration and command owner located, without claiming
  that tests pass;
- architecture: project boundaries, relevant edges, and source anchors.

Also stop when the last bounded action adds no new anchor or supported claim,
when the delivery budget is exhausted, or when the required index/provider is
unavailable. In the last case report the diagnostic; do not silently fall back
to an unscoped claim.

## 7. End-to-end implementation phases

### Phase 0 — Freeze the contract and baseline

Deliverables:

- this roadmap and a short planner contract document under
  docs/superpowers/plans/;
- a current MCP tool map generated from the connected server, including actual
  runtime tool names; do not hardcode a guessed mcp prefix;
- a baseline matrix for direct coding-agent exploration versus existing
  explore_context;
- five representative tasks: localize, explain, symbol impact, cross-project
  change, and test-surface discovery.

Exit gate: every task has an explicit required-evidence predicate and a known
baseline answer location. If the graph/index is unavailable, record that as a
baseline condition rather than treating source-only exploration as graph
validation.

### Phase 1 — Implement the main Skill

Files:

- .claude/skills/context-planner/SKILL.md
- optionally .claude/skills/context-planner/examples/ for packet examples and
  failure cases

Work:

- encode the contract, closed actions, first-call policy, stop/abstain rules,
  and no-edit/no-execution boundary;
- make explore_context the first CodeNib action for non-trivial tasks;
- require explicit project_id/file_path propagation whenever known;
- require inspection of scope, source, project_context, relationships, and
  diagnostics before a context claim;
- distinguish CodeNib’s deterministic retrieval plan from the high-level
  context-planner action decision.

Exit gate: the Skill can complete the five baseline tasks without subagents,
with bounded tool calls and explicit unsupported/ambiguous outcomes.

### Phase 2 — Add the three read-only subagents

Files:

- .claude/agents/scope-search.md
- .claude/agents/impact-navigator.md
- .claude/agents/evidence-auditor.md

Work:

- give each agent one narrow description, the complete read-only MCP surface,
  and a role-specific tool-routing policy;
- require the EvidencePacket format and compact output cap;
- pass paths, project IDs, graph seeds, unresolved diagnostics, and the exact
  question explicitly; isolated subagents do not inherit main conversation
  history;
- prohibit nested delegation and all mutations/execution;
- require a skip/unavailable/redundant reason for tools not used on a task;
- use a cheap model for scope-search; use a stronger model only for bounded
  multi-hop impact synthesis and evidence auditing.

Exit gate: each subagent works on fixtures containing duplicate names,
cross-project calls, missing graph providers, dynamic imports, and irrelevant
test directories. Packets remain parseable and under the agreed cap.

### Phase 3 — Integrate with the coding agent

Work:

- register the Skill and agents in the actual Claude/Codex project integration;
- verify MCP tool names from the live connection rather than copying a guessed
  prefix from the research artifact;
- keep the main agent responsible for edits, builds, tests, and final review;
- make the final packet available to the main agent as a compact pre-edit block;
- record planner actions and evidence IDs in the existing runtime trace/ledger
  path where an integration seam exists
  (codenib/agent/runtime/context.py and trace.py), without introducing a second
  persistence system.

Exit gate:

    task -> context contract -> explore_context -> optional delegation
         -> evidence audit -> main-agent edit -> main-agent test -> final review

No subagent edits the repository, runs a command, or claims a test result.

### Phase 4 — Evaluate and prune

Compare the same model, repository snapshot, and tasks across:

1. raw coding agent with normal file tools;
2. agent with direct CodeNib MCP tools;
3. agent with the context-planner Skill and no subagents;
4. agent with planner plus the three subagents.

Measure:

- task success and regression rate;
- implementation localization precision/recall;
- project-scope accuracy and false cross-project inclusion;
- supported-claim and unsupported-claim rates;
- unresolved/dynamic-edge reporting rate;
- useful-context ratio, source-window count, and bytes delivered;
- model tokens, tool calls, subagent calls, and wall-clock latency;
- edit churn and test-surface recall;
- abstention quality on unavailable, stale, ambiguous, and incomplete indexes.

Promotion rules:

- keep the planner only if it improves supported context or task success at an
  acceptable token/latency cost;
- keep a subagent only if its invocation has measurable value over the main
  Skill and direct MCP calls;
- if a subagent merely paraphrases explore_context, cut it;
- if graph traversal does not improve impact correctness, retain graph calls as
  on-demand deterministic tools and remove graph delegation from the default
  path.

### Phase 5 — Stabilize the public design

After evaluation, document the promoted contract, delete unused aliases and
planner-facing duplicates, and update the nearest design document with current
outcomes rather than implementation chronology. Do not promote experimental
subagent behavior into a CodeNib MCP API without a named consumer and tests.

## 8. Verification plan

### 8.1 Deterministic repository tests

If the implementation remains entirely in .claude/, validate frontmatter, tool
references, packet examples, and policy fixtures without billed model calls. If
CodeNib runtime code changes, start with the focused existing suites:

- test/mcp_server/test_explore_tool.py;
- test/mcp_server/test_server.py;
- dependency-tool tests;
- search-tool tests;
- explore bounds and explore-session tests;
- test/agent/test_context_ledger.py;
- test/agent/test_codenib_context.py where the existing composer is touched.

Then run the required unit tier:

    uv run python -m pytest \
      -m "not slow and not integration and not integration_serial and not integration_serial_consumer" \
      -x --tb=short

Run integration or model-dependent tiers only when the changed surface needs
them, and report external credentials/toolchain requirements separately.

### 8.2 Planner behavior fixtures

The fixture harness should assert action selection and packet semantics, not
exact prose. Required cases:

1. file path uniquely identifies a project;
2. explicit project ID is unavailable and retrieval is blocked;
3. duplicate project names are ambiguous;
4. workspace fallback is incomplete;
5. exact symbol needs only one source read;
6. impact requires symbol-level callers and project-level dependents;
7. dynamic/reflection edge is unresolved and never guessed;
8. graph provider is missing but indexed retrieval remains available;
9. stale source identity forces indexed evidence labeling;
10. no-new-evidence causes a bounded stop;
11. relevant tests/configuration are located without executing them;
12. subagent output exceeds its cap and is compacted or rejected.

### 8.3 Honest live-index verification

Before graph-specific evaluation, run the repository’s own codegraph status/init
path and confirm that the graph is current. A stale graph or missing language
toolchain must produce a visible environment diagnostic. A source-only run can
validate prompt flow and source citations, but cannot validate graph
completeness.

## 9. Safety and correctness invariants

1. Explicit scope wins. Never widen an unresolved explicit project request to
   the workspace silently.
2. No guessed edges. Unknown dynamic imports, reflection, generated code, and
   incomplete indexes become unresolved diagnostics.
3. Source authority is visible. Live verified source and indexed excerpts are
   different evidence classes.
4. One owner for mutation. Only the main coding-agent thread edits, builds,
   tests, installs, or changes indexes.
5. One owner for delegation. Only the main planner delegates; subagents cannot
   fan out.
6. Bounds are enforced below the prompt. CodeNib’s response byte cap, retrieval
   budgets, graph limits, and session ledger remain authoritative.
7. No silent fallback. Missing providers, stale artifacts, and incomplete scope
   remain in the final packet.
8. No duplicate planner. The Skill chooses evidence actions; CodeNib’s
   deterministic RetrievalPlanner chooses retrieval stages.
9. Stop is a first-class result. A smaller supported packet is better than a
   larger speculative context dump.

## 10. Non-goals and migration boundaries

- No new graph database, generic storage catalog, or object-store protocol.
- No graph schema bump for planner v1 and no change to C++ graph decoder parity.
- No separate identify_project or workspace MCP tool in this roadmap.
- No LLM planner embedded inside codenib/mcp; the first planner is an external
  coding-agent Skill using the stable MCP surface.
- No shell/build/test execution from context subagents.
- No second implementation of BM25, semantic retrieval, reranking, or graph
  traversal in Skill instructions.
- No raw chain-of-thought persistence or requirement for subagents to expose
  hidden reasoning.
- No automatic graph completeness claim when scope.complete or provider
  completeness is false.
- No expansion from three subagents to a team unless ablation shows a named
  missing capability and a measurable benefit.

## 11. References used for the design

- Current CodeNib MCP composition: codenib/mcp/tools/explore.py and
  codenib/mcp/server.py.
- Current deterministic scope resolution: codenib/graph/project_queries.py.
- Current deterministic retrieval path selection:
  codenib/model/retrieval_planner.py.
- Existing runtime evidence ledger: codenib/agent/runtime/context.py and
  codenib/agent/runtime/trace.py.
- Claude [Skills](https://code.claude.com/docs/en/slash-commands),
  [Subagents](https://code.claude.com/docs/en/sub-agents), and
  [Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents).
- Multi-agent tradeoff: [Anthropic’s multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system),
  especially its warning that breadth-first parallelism is expensive and is a
  poor fit for tightly coupled coding tasks.
- Context-engineering principles: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

The immediate next implementation slice is Phase 1: create the main
context-planner Skill and validate it against the five baseline tasks before
creating any subagent files. This preserves the LLM’s decision-making value
while giving the experiment a clean baseline for proving whether subagents
actually help.

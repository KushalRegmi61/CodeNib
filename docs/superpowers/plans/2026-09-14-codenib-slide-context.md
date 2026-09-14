# CodeNib: Monorepo-Aware Context for Coding Agents

## Slide 0 — Title, Scope, and Contents

### Title

**CodeNib: Monorepo-Aware Context for Coding Agents**

**From repository search to project-aware graphs, MCP tools, automatic indexing, and agent-guided context**

### Presentation scope

This presentation explains:

- The context problem in large monorepos.
- Existing repository-intelligence approaches.
- Why CodeNib was selected as the foundation.
- The implemented graph, MCP, indexing, Skills, and subagent extensions.
- The preliminary evaluation and its limitations.

The presentation focuses on the implemented CodeNib system and distinguishes shipped features from research proposals and experimental evaluation results.

### Contents

| Slide | Topic |
|---|---|
| 1 | Why large monorepos need better context |
| 2 | Existing repository-intelligence systems |
| 3 | Why we chose CodeNib and MCP |
| 4 | Monorepo graph extension |
| 5 | Operational and agent enhancements |
| 6 | Preliminary evaluation and limitations |

## Slide 1 — The Monorepo Context Problem

Large monorepos combine multiple applications, shared libraries, languages, and build systems in one repository. Important relationships are distributed across `package.json`, `pyproject.toml`, workspace manifests, source files, symbols, tests, and CI configuration.

Keyword search can find text, but it does not reliably answer:

- Which project owns this file or symbol?
- Which shared packages depend on it?
- Which applications and tests may be affected by a change?
- How much context is sufficient for the coding agent?

Agents therefore need bounded, source-grounded context before making changes rather than an unfiltered repository dump.

**Core question:**

> How can an agent identify the correct project, understand its dependencies, and estimate which other projects may be affected?

## Slide 2 — Existing Repository-Intelligence Systems

Compare JetBrains Context, GitHub Repo Mind, Repo Mind Light, LocAgent, CodePlan, RepoCoder, ContextBench, and CodeNib.

The common capabilities are:

- Lexical and semantic search.
- Symbol definitions and references.
- Call and dependency graphs.
- Repository summaries and graph-based retrieval.
- Incremental indexing.
- MCP-based agent access.
- Localization and planning before code generation.

The shared gap is not the absence of retrieval primitives. The harder problem is reliably choosing scope, expanding through dependencies, validating evidence, and stopping before context becomes excessive. Structural tools also only help when the agent actually adopts them in its workflow.

## Slide 3 — Why We Chose CodeNib

CodeNib was selected as the foundation because it already provides:

- BM25 lexical search.
- Semantic/vector retrieval.
- Sourcegraph Code Intelligence Protocol (SCIP) indexes.
- Language Server Protocol (LSP) navigation.
- Dependency traversal.
- Bounded source reads.
- MCP tools for Claude Code, Codex, and compatible agents.

Plain-language descriptions:

- **SCIP** represents code symbols, definitions, references, and relationships.
- **LSP** provides language-aware navigation such as definitions, references, types, and diagnostics.

```text
Coding agent
    ↓ MCP
CodeNib context server
    ├── search
    ├── SCIP/LSP navigation
    ├── dependency analysis
    ├── workspace/project scope
    └── bounded source context
        ↓
Monorepo
```

Selection rationale:

- Open-source and local-first.
- Already combines multiple repository views.
- Provides a common agent-facing boundary.
- Avoids introducing a second graph database.
- Offers strong seams for monorepo-aware extensions.

## Slide 4 — Graph Extension: From Code Files to Monorepo Architecture

### Real monorepo input

```text
monorepo/
├── package.json
├── pyproject.toml
├── apps/web/package.json
├── packages/shared-ui/package.json
└── services/api/pyproject.toml
```

### Generated architecture graph

```text
workspace://root
├── project://apps/web
│   ├── package.json
│   ├── files
│   └── symbols
├── project://packages/shared-ui
│   ├── package.json
│   ├── files
│   └── symbols
└── project://services/api
    ├── pyproject.toml
    ├── files
    └── symbols
```

### Core hierarchy

```text
workspace → project → file → symbol
```

The implemented graph extension adds:

- Workspace discovery from manifests.
- Stable workspace and project identities.
- `project_id` ownership on files and symbols.
- Internal manifest dependency edges.
- Project-level rollups of imports and symbol relationships.
- Deterministic project-scope resolution.
- Project-aware dependency and impact queries.

### Impact example

```text
Change shared-ui component
        ↓
find project dependents
        ↓
identify importing files and calling symbols
        ↓
locate affected applications and tests
```

**Impact:** the graph now explains not only where code exists, but how projects in the monorepo depend on one another.

Implementation evidence includes workspace discovery and architecture nodes (`2fe05687`), architecture-edge persistence (`1a4ae13b`), project-aware context queries (`d48123ca`), and deterministic project scope (`4e0836fe`).

## Slide 5 — Operational and Agent Enhancements

### Automatic freshness

```text
commit / checkout / merge / rebase
            ↓
managed CodeNib hook
            ↓
index committed HEAD
            ↓
validate new views
            ↓
MCP hot-swaps fresh views
```

The managed hooks keep the graph aligned with committed repository state, run in the background, avoid blocking Git operations, refresh MCP without a restart, protect foreign hooks, and track installation state.

### MCP enhancements

- `explore_context` for bounded, source-verified context.
- Project-aware `dependency_subgraph`.
- `find_projects_using`.
- Project/file-scoped retrieval.
- Lazy loading of index views.
- Workspace and manifest status reporting.

### Claude Code Skills and subagents

```text
Task
  ↓
context-planner
  ├── scope-search
  ├── impact-navigator
  └── evidence-auditor
  ↓
bounded evidence packet
  ↓
main coding agent
```

The planner selects only the evidence needed for the task:

- `scope-search` identifies relevant projects and files.
- `impact-navigator` follows graph relationships.
- `evidence-auditor` checks source, tests, provenance, and gaps.
- Subagents remain read-only.
- Ambiguous or stale evidence produces an abstention instead of a guessed answer.

**Impact:** CodeNib moved from a static repository index to a continuously refreshed and agent-guided context service.

## Slide 6 — Preliminary Evaluation and Limitations

The evaluation compared three conversation-history branches. It was a small engineering test, not a complete benchmark of the CodeNib graph, MCP, hooks, or planner system.

| Branch | Score | Reported total tokens |
|---|---:|---:|
| `feat/conversation-history-raw` | 76/100 | 112k |
| `feat/conversation-history-codenib-withskills` | 74/100 | 144k |
| `feat/conversation-history-graphify` | 71/100 | 136k |

Findings:

- All branches passed the reported backend lint, frontend ESLint, TypeScript, and focused backend checks.
- The raw branch was recommended as the best starting point.
- None was merge-ready without follow-up fixes.
- Token count is reported for context only and was not used as a quality metric.

Limitations:

- The test was minor and focused.
- It evaluated conversation-history implementations, not the complete CodeNib system.
- Results may be biased by limited usage limits on open-source tools.
- OpenCode behaved differently and did not properly use the Claude Code Skills.
- No frontend tests were present.
- The comparison was not a controlled benchmark of agent task success.

**Closing message:**

> CodeNib now combines monorepo-aware graph context, MCP retrieval, automatic index freshness, and bounded agent orchestration. A larger controlled evaluation is still needed to measure task success, context precision, latency, token cost, and actual tool adoption.

## Source and evidence notes

- `deep-research-report.md` supplies the comparative research and motivation.
- `existing_system_report.md` supplies the original graph-extension and planner framing; current implementation claims are reconciled against repository documentation and commit history.
- `conversation-history-branch-evaluation.md` supplies the scores, token counts, validation results, and evaluation caveats.
- `docs/codegraph.md` is the primary current product documentation for graph setup, workspace/project overlays, MCP usage, hooks, and automatic updates.
- Key implementation commits: `2fe05687`, `1a4ae13b`, `d48123ca`, `4e0836fe`, `50209f39`, `db7302f3`, `a7e05e1d`, `757d25f4`, `3aa1f1c3`, and `2755efbc`.

# SOTA Repository-Intelligence Systems

**JetBrains Context (July 2026)** – A production repository-intelligence layer for coding agents (Codex CLI, Claude Code, Junie). Incrementally indexes repos to support *semantic search* and “Ask” queries. Reports up to 68% fewer agent turns and 59% lower latency on large benchmarks. Uses server-side indexing (likely vector embeddings and text indexes) and exposes CLI/agent hooks. Focus is on efficient semantic lookups (concepts, APIs, code answers) rather than providing specialized task orchestration. It demonstrates that scoped repo context dramatically aids agent performance, but does not detail a dynamic “next info” policy.

**CodeNib (2026)** – Open-source multi-view repository-context system (Apache-2) implemented in Python.  Builds per-commit *lexical* (BM25/Zoekt), *dense* (embeddings vector store), and *structural* views (symbol graph, SCIP/LSP navigation) of code. Exposes an MCP server with tools (search, definition, references, call graph “route”, dependency subgraph, bounded source fetch) and even a high-level skill `explore_context` to iteratively retrieve context. CodeNib reuses AST/Scip for code analysis, HuggingFace models for embeddings, and maintains incremental indices (8–25× speedup updates). It includes a built-in *Context Explorer* with policies (“auto”) to assemble *bounded, source-verified* context by combining semantic search, symbol navigation, and dependency expansion under a token budget. CodeNib can serve as a base substrate: it already integrates lexical, semantic, and graph-backed retrieval and provides an agent-facing interface with context bounding. It lacks explicit project/workspace knowledge, multi-project graphs, or CI/build intelligence, but these could be layered on or added by providers.

**GitHub Repo Mind (Mar 2026)** – Research prototype (GitHub Next) for *holistic repo understanding*. Builds a two-part index: (1) a semantic retrieval layer (vector embeds of code, documentation, issues/PRs) and (2) a GraphRAG-style cluster graph of code entities. Uses Tree-sitter to extract top-level declarations and call/subtype edges. Summarizes declarations and other artifacts to enable cross-file understanding. At query time it retrieves relevant code/doc chunks via vectors and augments answers with global graph context (precomputed cluster summaries or on-the-fly GraphRAG). Agents can also call LSP-like tools (symbol search, type hierarchies). Evaluation: improved multi-file tasks on SWE-Bench Pro (especially consistency and for harder tasks). Key findings: broader tasks benefit most, but *tool adoption* matters – structural tools only helped when the agent used them, and they must fit the agent’s workflow. Repo Mind is closed-source, but shows value of combining *semantic search*, *structural graph*, and *global summaries* for broad tasks. It suggests an architecture with both vector retrieval and graph-clustering, which informs building similar multi-modal retrieval.

**Repo Mind Light (Aug 2026)** – GitHub Next system focusing on *historical/operational context*. Incrementally indexes issues and PRs into a local index (100 MB typical for thousands of items) and uses live code/doc retrieval from GitHub. Employs GraphRAG-Zero (proprietary) to combine issue/PR context with code/docs at query time. Exposes context via a local MCP server (Docker image) for agents. Use case: internal incident response (find related issues, contacts, tribal knowledge in discussions). Highlights that *hidden knowledge* in issues/prs can be crucial. Agents can call this to retrieve broad repo memory. Source: closed, but shows how non-code data (issues, PRs, docs) can be integrated on demand. Repo Mind Light is focused on QA and context, not on code changes per se.

**Graphify (2024)** – Commercial knowledge-graph service (Graphify Labs, YC S26, 100K stars) that ingests entire project (code, docs, PDFs, images, etc.) into a graph. Queries via AI: “turns project into queryable KG” (says marketing). Likely uses vector embeddings plus graph structure. Proprietary. Distinct from CodeNib in breadth of artifacts (image/PDF support), but details unknown. Likely out of scope for reuse. Serves as an example of knowledge-graph approach: index once, query many.

**LocAgent (ACL 2025)** – Academic system for *code localization*: parse code into a heterogeneous directed graph (nodes: files, classes, functions; edges: imports, calls, inheritance) to guide LLM agents in locating relevant code sections before generating fixes. By framing localization as multi-hop graph queries, it outperforms baselines: ~92.7% file-level recall and 86% cost reduction with Qwen-2.5 (comparable to SOTA proprietary). Key insight: *localization before generation*, using graph-based multi-hop search. Suggests our planner should do a graph-based localization step first.

**CodePlan (FSE 2024)** – Microsoft Research. Targets “repository-level coding” (e.g. migration, fixes affecting many files). Treats it as a planning problem: synthesizes a chain of edits where each step calls an LLM with context from the whole repo, previous edits, and instructions. Uses *incremental dependency and impact analysis* to determine which parts to edit and in what order, adapting the plan as it goes. Evaluation: on multi-file tasks (C#, Python), achieved valid builds on 5/6 repos vs 0/6 for non-planning baselines. Relevance: introduces dependency analysis & multi-step planning. We’ll borrow its idea of using static dependency analysis to expand context and to structure the sequence of LLM calls.

**RepoCoder (2023)** – Paper on *iterative retrieval* for repo-level completion. Describes a loop: retrieve code, generate, incorporate new info, retrieve again, etc. Highlights that *RAG shouldn’t be one-shot*: each generation can inform further retrieval. (No direct citation found; concept from prompt.) We will adopt this *retrieve–generate–refine* workflow in our context engine.

**Agentless (arXiv 2407.01489)** – Argues not everything needs an agent. Prescribes a pipeline: **localization → repair → validation**. e.g. determine target code, fix it, then test it. This supports using deterministic tools (e.g. static analysis, test suites) for parts of the task, rather than always LLM. We plan to use a similar split: context engine handles localization (information retrieval) deterministically, agent/LLM handles reasoning/fixes, then validation (e.g. running tests) confirms.

**ContextBench (2026)** – Benchmark (66 repos, 1136 tasks) for context retrieval in coding agents. Human-annotated “gold contexts” per task, metrics: recall/precision/efficiency. Finding: a large gap between context retrieved and context actually needed. More context is not always better (inefficient). This empirically underpins our thesis that *minimum sufficient context* is key (we will use “useful/context ratio” in metrics).

**Other Systems/Papers:** 

- **Graph-Databases (CodexGraph 2024):** Uses code graphs in a graph DB (Neo4j) to answer LLM queries via Cypher. Similar to Prometheus. Not yet detailed, but shows trend of *queryable code graph* with LLM interface.
- **RepoGraph (2024)**: Code graph tool (GitHub), baseline for code graph queries.
- **Prometheus (2025)** – Builds a *unified knowledge graph* from code (AST), text, etc., for issue resolution. Typed nodes, 5 edge types, multi-language support. Uses Neo4j and LLMs (DeepSeek) to resolve issues (28.7% on SWE-bench Lite). Demonstrates scaling a graph DB over multi-language repos.
- **SWE-bench (2023)** / **SWE-bench Pro (2025)** – Benchmarks for repo-level code tasks, used in many evaluations (JetBrains, Repo Mind, Prometheus, etc.).
- **RepoMind (Repos with retention)** – Example tasks across threads. Emphasize need for broad context.
- **LocAgent** & **ARISE (2025)** – See above; ARISE also introduces data-flow slicing for bug localization (Python-specific). ARISE builds a multi-tier API for search, slicing, context assembly, achieving much higher bug-localization recall. It is a model of fine-grained context retrieval.

A **comparison matrix** of these systems (capabilities vs. features) is provided at the end of this document. 

# Capability Decomposition

We enumerate needed capabilities and existing solutions:

- **File/AST parsing**: *Tree-sitter*, *Scip*, *Language-specific parsers*. Reuse: Yes (e.g. CodeNib uses SCIP for TypeScript; GitHub uses Tree-sitter). Our: integrate existing parsers per language.
- **Symbol navigation (definitions/references)**: SCIP, LSP. Reuse: Yes (e.g. CodeNib’s toolchain, Repo Mind using Tree-sitter). We will use existing LSP/SCIP engines via CodeNib or LSP servers.
- **Call graph / Inheritance graph**: Structural analysis. Reuse: Partially (CodeNib’s “route” tool; LocAgent builds full call graph). Can use CodeNib or other graph generator.
- **Lexical search (grep-like)**: Tools like Zoekt, grep, TF-IDF. CodeNib uses Zoekt/BM25. Reuse: Yes; CodeNib has BM25 & regex tools. We adopt Zoekt or similar.
- **Semantic search (vector retrieval)**: CodeNib supports dense embeddings; GitHub uses vectors in Repo Mind. Reuse: Yes; use Faiss/Milvus or CodeNib’s LLM embeddings. Possibly use open models (CodeBERT, Llama embeddings).
- **Hybrid reranking**: CodeNib uses BM25+dense fusion, query rewriting. Might reuse CodeNib’s design.
- **Dependency graph (package/monorepo)**: Nx, Bazel project graph, cargo/npm graphs. Reuse: Partial. No single tool covers all. Nx provides TypeScript/JS workspace graph. Bazel has build graph. We might adapt Nx’s logic generically, or use `scip` manifests, language package info. No full SOTA open solution; we need to integrate multiple.
- **Build/Test graph**: No off-the-shelf multi-language. Could parse `nx.json`, `package.json`, `Cargo.toml`, `Makefile`, CI configs. Likely implement custom adapters or use Bazel/Nx libs.
- **Documentation/README indexing**: CodeNib has `codenib wiki` to index docs. GitHub search for docs. Reuse: Yes, use CodeNib wiki or mkdocs integration.
- **Issue/PR context**: Repo Mind Light indexes issues; Prometheus uses them. For now, optional; we’ll omit or later integrate via GitHub API.
- **Interactive retrieval tools**: CodeNib (MCP), GitHub (CodeQL?), GraphQL API, etc. We’ll rely on CodeNib’s tools and LSP/grep.

- **Context Planner**: *Our contribution.* No existing fully general solution. CodeNib has `RepositoryContextExplorer`, but we need research-weight planning: multi-step action selection under budget. Possibly extend CodeNib’s explorer or devise new policy.

- **Context Pruner/Ranker**: Research gap. Some ideas from ContextBench (we want high precision). We will design relevance scoring + redundancy filtering (e.g. maximal marginal relevance).
- **Budgeting**: Formal aspect of planner. Likely novel.

- **Agent integration**: CodeNib is agent-agnostic but uses MCP. JetBrains has its own clients. We plan a tool interface or CLI wrapper. CodeNib suggests using MCP, but we may start with CLI or local API and allow MCP later. 

**Reuse Summary Table (Capability → Existing SOTA → Reuse? → Gaps → Our work):**

| Capability                      | Example SOTA                       | Reuse?     | Gaps/Missing                 | Our Work                    |
|---------------------------------|------------------------------------|------------|------------------------------|-----------------------------|
| AST parsing                     | Tree-sitter, SCIP (CodeNib)        | Yes        | Multi-language grammar care   | Adapter interface           |
| Lexical search                  | grep/Zoekt, CodeNib BM25          | Yes        | None major                    | Use directly                |
| Semantic (vector) search        | CodeNib dense, LLM embeddings       | Yes        | Integration & tuning         | Use precomputed embeddings  |
| Symbol navigation               | LSP, SCIP                          | Yes        | Depends on language support   | Use as provider             |
| Call/Import/Type graph          | CodeNib "route", Graphify         | Yes (partial) | Multi-language cross-calls    | Integrate via SCIP/LSP + Graph DB |
| Project/workspace graph         | Nx project graph, Bazel graph      | Partial    | No universal tool for all languages | Adapter abstraction         |
| Dependency graph (packages)     | `cargo`, `npm`, `pip` analyzers    | Partial    | Integrating polyglot manifests | Write provider adapters     |
| Build/test graph                | Bazel, Nx, Task definitions        | Partial    | Heterogeneity of CI systems   | Abstract via providers      |
| Localization (initial code find)| LocAgent (graph), CodeNib bm25    | Yes        | Optimal policy?              | Implement graph-based first steps |
| Impact/dependency analysis      | CodePlan (static analysis)         | Yes        | Generalizing beyond a few languages | Adapt for expansion       |
| Retrieval (multi-modal)         | CodeNib, RepoMind                 | Yes        | Orchestration missing         | Orchestration logic         |
| Context planner/policy          | *none full* (CodeNib explorer partial) | **No** (novel) | How to value actions, budget| Core: implement heuristic or learned policy |
| Context pruning/ranking         | *Sparse prior art*                | **No**       | Need relevance model         | Design scoring/prune rules  |
| Budget management               | *Not addressed by others*        | **No**       | Latency & token costs        | Model as constrained optimization |
| Incremental indexing            | CodeNib, JetBrains (likely)       | Yes        | Might need adaptation         | Use CodeNib’s incremental engine |
| Agent interface (MCP/CLI)       | CodeNib, JetBrains CLI integration | Yes        | Standardization (MCP optional)| Provide CLI or MCP adapter  |
| Evaluation (benchmark tasks)    | SWE-bench, ContextBench          | Yes (for metrics)| Focus on context metrics    | Use existing benchmarks     |

This shows most primitives exist in SOTA; **our novel contribution** is the **Task-Adaptive Orchestration layer** (planner+resolver+ranker).

# Proposed Architecture

**Overview:**  We will build an *Agent-Agnostic Context Engine* that sits between a coding agent and the repository. Its components:

- **Task Understanding (Agent Request)** – Interface from agent requests (via CLI/tool invocation or hook). Provides initial *localization cues* (keywords, file hints).

- **Context Planner** – Core orchestrator. Maintains state (discovered relevant entities, remaining budget) and decides **next action**: which retrieval/navigation to perform (e.g. semantic search, symbol find, call graph expansion) and with what parameters. Uses heuristics combining expected information gain vs. token/latency cost (e.g. simple scoring, or learned weights).

- **Context Resolver** – Executes retrieval/navigation actions against the **Monorepo Intelligence Layer** (below). Retrieves candidate items (files, symbols, commits) and evidence.

- **Context State/Cache** – Accumulates known relevant context (file windows, code snippets, symbols, tests, docs). Marks items as *selected, explored, excluded*. Maintains used token count and latency.

- **Monorepo Intelligence Layer** – Underlying providers:
  - *Code Intelligence*: language-specific analysis (Tree-sitter/Scip) for AST, symbols, call/import graphs.
  - *Project/Workspace Intelligence*: package/project detection (Nx workspaces, Bazel/Cargo projects), module/package dependency graph.
  - *Dependency Intelligence*: language package dependencies (cargo, npm, pip), cross-language binding info.
  - *Build/Test Intelligence*: known build targets, test targets, CI config.
  - *Documentation/Git/Issue Intelligence*: README, docs, code comments. (Optional for now).
  - *Search Indices*: Lexical and semantic indexes built over the repo.

The **data flow**:

```
 Developer Task Request
           ↓
      Coding Agent
           ↓ (asks for context)
   Task-Adaptive Context Engine
   ┌────────────────────────────┐
   │ Task Understanding        │
   │ Context Planner           │
   │ Context Resolver          │
   │ Context State (cache)     │
   └────────────────────────────┘
           ↓ (selected context)
       Agent uses context
           ↓
   (agent may ask for more → loop back)
           ↓
     Repo Intelligence Layer
  (search indices, graphs, code analysis)
           ↓
         Repository
```

No single monolithic graph or DB is mandated; rather, a set of views and indexes held by CodeNib or similar provides answers. The engine coordinates calling them.

# Canonical Data Model

We define entities and relations to represent the repo (language-agnostic):

- **Repository**: root container.
- **Workspace**: a top-level grouping (e.g. Nx workspace, Bazel workspace).
- **Project / Package**: user-defined project or package (e.g. npm package, Cargo crate, Python module). Fields: type, name, build targets.
- **Directory / Module**: folder or package/module as per language (namespaces).
- **File**: source file (path, language, content).
- **Symbol**: type/class/enum/interface, or top-level function, constant, etc. (with name, kind).
- **Function/Method**: code block or method (with signature).
- **Test**: test target or test file.
- **Configuration**: e.g. Nx config, package.json, CI config.
- **Documentation**: README, design docs.
- **Issue/PR** (optional): ticket text.

Relationships (edges):

- `CONTAINS` (Workspace→Project, Project→File, Directory→Symbol, Class→Method, etc).
- `IMPORTS` (File→File, Module→Module; or Symbol→Symbol via import/use).
- `CALLS` (Function/Method→Function/Method).
- `DEPENDS_ON` (Project→Project via package management, or File→File via code inclusion).
- `DEFINED_IN` (Symbol→File).
- `REFERENCES` (Symbol/Function→Symbol/Function for textual references).
- `IMPLEMENTS` (Class→Interface, Function→Abstract method).
- `SUBTYPE_OF` (Class→Class for inheritance).
- `TESTS` (Test→File or Symbol under test).
- `BUILDS` (Project→BuildTarget, BuildTarget→File list).
- `GENERATES` (BuildTarget→GeneratedFile).
- `RUNS_IN` (File/Executable→BuildTarget).
- `HAS_ISSUE` (File/Symbol→Issue/PR mentions).

This graph can be stored implicitly (CodeNib’s views) or in a graph DB if needed. But we can also keep separate indexes and infer edges on the fly (via queries).

# Context-Orchestration Algorithm

We frame the planner as an iterative decision loop. Pseudocode:

```
state = { budget_tokens=B, budget_time=T, known_context = ∅, discovered_entities=∅ }
task_keywords = extract_keywords(task_description)

# Initial localization (graph-guided)
initial_targets = find_symbols_or_files(task_keywords)
state.discovered_entities += initial_targets
context = rank_and_limit_context(initial_targets, state)

while agent_not_done and budgets remain:
    # 1. Identify next possible actions (Candidate actions)
    actions = []
    for each entity in state.discovered_entities (file, symbol, project):
        actions += [
          SearchAction(query=keywords or entity.name),
          SymbolSearch(entity.name),
          CallExpansion(entity), 
          ImportExpansion(entity), 
          ImplementationSearch(entity.interface), 
          DependentProjects(entity.project), 
          BuildTargets(entity), 
          TestTargets(entity),
        ]
    # 2. Score each action: Value = InfoGain - α*tokenCost - β*latency - γ*redundancy
    for action in actions:
        action.value = estimate_info_gain(action) - weight1*estimate_token_cost(action)
                        - weight2*estimate_time_cost(action) - weight3*redundancy_penalty(action, state)
    # 3. Pick best action (greedy) or multi-step planning (beam).
    best_action = argmax(actions, key=value)
    if best_action.value <= threshold or budget exceeded:
        break
    # 4. Execute action via Context Resolver:
    results = Resolver.execute(best_action)
    # 5. Update state: add new discovered_entities from results
    state.discovered_entities += extract_entities(results)
    # Add retrieved code snippets to known_context
    state.known_context += select_relevant_snippets(results, remaining_budget)
    # 6. Prune / dedupe context if needed:
    state.known_context = prune_context(state.known_context, state)
    # Deduct tokens/time from budgets
    B -= tokens_used(results); T -= elapsed_time
end

return state.known_context (to agent)
```

Key elements:
- **Information gain**: Score actions by how much new relevant context they likely add (e.g. number of matched lines or new symbols).
- **Token cost**: Estimate by number/length of results and how much would be sent to agent.
- **Latency cost**: Retrieval time. Could treat all actions equally or roughly (dense search slower than text search).
- **Redundancy**: If an action yields context already known, value drops.
- **Thresholding**: Stop when no high-value action remains or budgets exhausted.

We will likely start with a simple heuristic policy (“policy=auto” like CodeNib) and refine:
- For example: **High-level plan**:
  1. Use *semantic search* on task keywords to find relevant files.
  2. If found, use *structural navigation* on those files: e.g. find definitions, references of important symbols.
  3. Expand along *dependency graph*: find imports or callers from those files (multi-hop).
  4. Include tests and build targets for affected files.
  5. Stop when no new info or context window full.

We will refine with weights and possibly ML from ContextBench-like data.

# Implementation Roadmap

- **Phase 0**: Baseline & SOTA reproduction. Install CodeNib, test with a coding agent (e.g. Codex CLI) on a sample repo. Reproduce JetBrains Context and RepoMind results qualitatively.
- **Phase 1**: Repository ingestion. Use CodeNib to index a monorepo. Extend for non-Python languages by adding Tree-sitter grammars / SCIP plugins (CodeNib supports Python/TS out-of-box). Detect projects (e.g. parse package.json, pyproject, Cargo.toml).
- **Phase 2**: Canonical repository representation. Define our entity schema (as above). Possibly represent as in-memory objects or serialized. Ensure we capture AST-derived symbols, project modules, dependencies.
- **Phase 3**: Retrieval providers. Wrap CodeNib tools (bm25, dense, symbol query, call graph). Implement adapters for project graph: e.g. custom tool to answer “which projects depend on this package” (using manifest info). Add any missing intelligence (e.g. build/test introspection via parsing configs).
- **Phase 4**: Initial task localization. Given a natural-language task, extract key terms (names of features, classes). Use semantic search + symbol search to find starting code entities.
- **Phase 5**: Context planner. Implement the loop above. Simple heuristic scoring. Integrate with CodeNib’s `RepositoryContextExplorer` or build our own Planner class. Maintain session state.
- **Phase 6**: Expansion & pruning. After initial localization, expand via graph: callers, callees, imports, dependents, tests. Then filter out unrelated context. Implement a relevance scoring (e.g. cosine relevance to task, or LLM scoring).
- **Phase 7**: Agent integration. Expose as CLI or tool library. For example, a CLI: `context-engine --task "..." --repo /path` that outputs a context file for the agent. Or integrate as an MCP toolset. Ensure agent can call for more context mid-task.
- **Phase 8**: Demo on a real repo. Choose a mid-size polyglot open-source monorepo (e.g. a popular Nx workspace or a multi-language web-service). Test a real feature request end-to-end.
- **Phase 9**: Evaluation. Compare against baselines (no context, CodeNib context without orchestration) on tasks from SWE-Bench or context-bench. Measure success, tokens, time, context efficiency.

At each phase, use metrics (task success, token count, latency, context precision) to validate progress.

# Avoid Overbuilding

**Do NOT build**:
- A new general-purpose LLM or fine-tuned model.
- A new coding agent: we integrate existing ones (Codex CLI, Claude, etc.).
- New full language parsers: reuse Tree-sitter/SCIP.
- A new dense-vector store from scratch: use existing (Faiss/Milvus via CodeNib).
- A new large graph database: we can use CodeNib’s in-memory or a simple graph; heavy DB (Neo4j) is optional later.
- A giant full evaluation framework: use existing tasks and small prototypes.
- Rely on Model-Context Protocol (MCP) for V1 (optional V2).
- Hard-code to one language or monorepo tech (no Nx-only). Must abstract language/workspace.
- A generic LLMQA system or summarizer: focus on retrieval planning.

We reuse: CodeNib core, SCIP/LSP providers, existing search tools. We do **not** rebuild these foundational pieces.

# Novelty Assessment

Most underlying repo-intel capabilities are available: indexing (CodeNib), graph navigation (SCIP, Graphify, LocAgent), semantic search (CodeNib), project discovery (limited in SOTA), etc. **Novelty lies in the orchestration layer**. Existing systems either (a) provide context statically (semantic search, graphs), or (b) list tools for agents to call manually (Repo Mind). What is *not* solved is *“when and how”* to choose and sequence those context queries for a given task under cost constraints. 

- JetBrains Context / CodeNib / Repo Mind **provide context tools**, but do not explicitly solve *task-adaptive retrieval policy*. They give retrieval primitives; our question is how to schedule and limit them. 
- CodeNib’s `explore_context` is a step in that direction, but it uses a fixed strategy. We plan a more general planner.
- Repo Mind and Prometheus focus on broad repo comprehension, not on dynamic budgets.
- LocAgent does pre-localization via graph, which we will integrate.
- CodePlan does planning of edits, but not interactive retrieval strategy for context.
- RepoCoder suggests iterating retrieval/generation, which we will follow.
- Agentless suggests splitting to non-agent steps, aligning with our deterministic context collection.

Thus, our **potential novelty**: A *language-agnostic, monorepo-aware context orchestrator* that composes existing search/navigation providers in a task-sensitive way to maximize usefulness per token/time. This orchestration (planner + budgeting + dynamic expansion/pruning) appears to be insufficiently addressed by current work. If this hypothesis fails (others already cover it), we will pivot rather than forcing it. JetBrains and GitHub Next both underscore that mere tools aren’t enough; policy is key.

# Implementation Recommendation

**Base system:** Begin with **CodeNib** as the substrate. It is open-source (Apache-2), actively developed, and already integrates the essential multi-view indexing and tools we need. It provides:
- Lexical search (Zoekt/BM25) and dense retrieval.
- Graph-based navigation (symbol definitions, references, call graph via SCIP).
- Context bundling tools (`explore_context` and MCP interface).
- Incremental indexing.

We can extend CodeNib by:
- Adding providers for build/test/project graph (e.g. parse workspace configs).
- Customizing the `RepositoryContextExplorer` policy or replacing it with our planner.

If CodeNib were unsuitable (e.g. lacking performance or features), we would fallback to building atop SCIP + Zoekt + Faiss manually, but CodeNib saves enormous effort.

**Component integration:** 
```
Existing SOTA (CodeNib, LSP, embeddings) 
        ↓ reuse/adapt
   Our Context Orchestrator
        ↓ 
    Coding Agent 
```
We will adapt CodeNib’s IPC or Python API for queries. The orchestrator will be a new layer (likely in Python too) that calls CodeNib’s index and tools for each chosen action, assembling results into a context packet for the agent. 


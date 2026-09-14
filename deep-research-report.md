# System Overview  
We extend CodeNib into a **monorepo-aware context engine** that layers workspace/project intelligence on top of its existing code-indexing. The core is an **extended igraph** that includes workspace and project nodes in addition to files and symbols.  During repository ingestion, CodeNib’s compiler will:  
- **Parse the root manifest** (e.g. `pnpm-workspace.yaml`, `Cargo.toml` workspace, etc.) and create an `L_workspace` node for the repo.  
- **Discover sub-project manifests** (e.g. package.json, pyproject.toml, Cargo.toml in `apps/*`, `libs/*` directories) and create an `L_project` node for each.  Each project node is linked to the workspace node with a `contain` edge.  
- **Build symbol graph (existing)**: For every source file, CodeNib already creates an `L_0` file node and `L_2` symbol nodes via SCIP/tree-sitter. We extend this by tagging each file/symbol with its `project_id` (derived from path).  
- **Add dependency edges**:  
  - **Containment**: `$L_{\text{workspace}}\xrightarrow{\text{contain}}L_{\text{project}}$` and `$L_{\text{project}}\xrightarrow{\text{contain}}L_0$`.  
  - **Manifest dependencies**: If project A’s manifest declares a dependency on project B, add `$L_{\text{project_A}}\xrightarrow{\text{depends\_on\_manifest}}L_{\text{project_B}}$`.  
  - **File imports**: Already in CodeNib’s structural graph as `$L_{0,A}\xrightarrow{\text{imports}}L_{0,B}$.`  
  - **Symbol calls**: Already as `$L_{2,A}\xrightarrow{\text{calls}}L_{2,B}$.`  
- **Aggregate edges by project**: The context runtime can group symbol-level calls by `project_id` to infer `$A\to B$` project-level edges on the fly without storing duplicate graph.  

This yields a **single igraph** spanning four tiers: `L_workspace → L_project → L0 (file) → L2 (symbol)`. It supports queries like “which projects depend on this library” and “what callers of this function span projects”. We do **not** build a separate graph DB (avoids duplication); we simply **enhance CodeNib’s graph builder and MCP query layer** (as suggested in [the design notes above]).  

**Key citations:** CodeNib already supports structural symbol graphs and dependency queries; JetBrains Context shows the value of semantic indexing for agents; and CodePlan highlights that agents need architecture (dependency) maps since LLMs alone can’t build them.  

# Extended Graph Schema  
Our **4-tier node schema** will be:  
- **L_workspace**: Root workspace manifest (example ID “workspace://root”). One per repo.  
- **L_project**: Each application or shared package (ID “project://<path>”). Attribute: `is_shared` (true if used by multiple projects).  
- **L0 (File)**: Existing CodeNib file nodes (e.g. “src/auth.ts”). Attribute: `project_id`.  
- **L2 (Symbol)**: Existing CodeNib symbol nodes (e.g. “file::functionName”). Also tagged with `project_id`.  

Edges:  
- **contain(workspace→project, project→file, file→symbol)** (built by compiler).  
- **depends_on_manifest(project→project)** from declared manifest dependencies.  
- **imports(file→file)** from code-level imports (existing).  
- **calls(symbol→symbol)** from code-level calls (existing).  

This lets an agent ask, for example, “what uses shared-lib X?” by querying incoming `imports` or `depends_on_manifest` edges to the node for that library’s project. If the agent asks “if I change function f, which projects break?” it finds the symbol node for f, follows incoming `calls` and containment to identify affected `L_project` nodes.  

A **roll-up query** can aggregate all `$L_{2,A}→L_{2,B}$` calls to show `$L_{\text{project}_A}→L_{\text{project}_B}$` edges. CodeNib’s dependency_subgraph tool will gain a `granularity="project"` mode, and a helper MCP tool (e.g. `find_projects_using(symbol)`) can wrap these queries.  

# Context-Planner Skills (Subagents)  
We design a set of **agent skills (tools)** and an orchestration (planner) to navigate the graph and retrieve context under token/latency budget. Key skills include:  

- **Project/Workspace Discovery:** Given a task or file path, identify the owning project and workspace by scanning root and sub-manifests. Returns project name(s). (This solves which part of the monorepo is relevant first.)  

- **Scope-Limited Search:** Run `search_semantic`/`search_bm25` only within a project’s files (e.g. by filtering search results to that subtree). This “zoom-in” confines context to the relevant project. It uses CodeNib’s existing search views but with path filters.  

- **Dependency Navigator:** From a symbol or module, use the graph to find cross-project edges. For example, a “who_imports” or “project_dependents” query: follow `imports` or `depends_on_manifest` edges backwards to list projects/files that use this module. This is a “zoom-out” to see impact elsewhere. (CodeNib’s symbol graph and `dependency_subgraph` already support call-tracing; we extend it to treat imports at module level.)  

- **Call-Graph Expansion:** Multi-hop traversal skill (like LocAgent). Given a set of starting symbols and a depth, follow `calls` edges forward or backward to get a subgraph of related functions. Allows iterative refine: e.g. find initial candidates via search, then traverse call/inheritance graph for more context.  

- **Test Locator / Build Context:** Given a code area or change, find relevant tests and build targets. For example, parse project configs or CI files to find test patterns (e.g. files matching `*Test.java` for Java, or identify npm scripts/test commands). This helps ensure we retrieve tests/CI jobs for any changed projects.  

- **Context Planner (Orchestrator):** A high-level controller that decides which skill to invoke next based on current context and task. It tracks remaining token/latency budget. The planner can be an LLM-assisted policy or deterministic logic: it might take the task description plus evidence gathered so far and decide, for example, “if module X was found, now run Call-Graph Expansion on its interface; else perform Scope-Limited Search for feature Y”. This is akin to a sub-agent or chain-of-thought guiding retrieval. Crucially, it aims to **minimize context**: only fetch new info if it likely increases “information gain” minus cost.  

These skills work together: e.g. start with Project Discovery, then Scope-Limited Search; if unclear, Context Planner may ask Dependency Navigator to see if other projects use related code; then Call-Graph to gather callers or implementers; then fetch code/text via `explore_context` as needed. The planner enforces token/time budgets by pruning low-value context and stopping when enough evidence is gathered.  

# Implementation Plan (Stepwise)  
**Phase 0: Setup & Baselines** (Weeks 0–1)  
- **Tools:** Install CodeNib, ensure it builds on target repos (preferably in Python, TS, etc). Integrate Codex/Claude via MCP per CodeNib docs.  
- **Baseline Agent:** Use a standard coding agent (Codex CLI or Claude Code) with CodeNib’s default context serving (without our extensions) as a control.  
- **Deliverables:** Working environment; run CodeNib on a sample monorepo to index it (BM25 + symbol graph). Confirm existing MCP tools (`explore_context`, `dependency_subgraph`) work end-to-end.  

**Phase 1: Extend Graph Ingestion** (Weeks 2–3)  
- **Manifest Scanning:** Modify CodeNib’s View Compiler to detect workspace and project manifests. For each repo, parse the root manifest to create an `L_workspace` node. Then parse subdirectory manifests to create `L_project` nodes. (This logic can reuse monorepo tool helpers from Nx or Bazel if available, or custom YAML/TOML parsing.)  
- **Containment Edges:** For each project node, link it to `L_workspace`, and link every file under that project directory to the project (existing CodeNib links file→project). Add attribute `project_id` to every `L_0` and its symbols.  
- **Dependency Edges:** From each project’s manifest, read declared internal dependencies. For each, add `depends_on_manifest` edge between the corresponding project nodes.  
- **Storage:** Use the same igraph graph store (e.g. `graph.pkl`) – no new DB. Update the Repository Manifest metadata to include workspace/project info.  
- **Testing:** Index a sample monorepo (e.g. a JS/TS monorepo or a Python multi-package repo). Verify the igraph contains workspace, project, file, symbol nodes (graph API, or CodeNib visualization UI if exists).  

**Phase 2: MCP Tool Enhancements** (Week 4)  
- **dependency_subgraph granularity:** Extend the existing `dependency_subgraph` tool (in the MCP serving runtime) with an option `granularity="project"`. When invoked, the igraph query groups call/import edges by `project_id` and returns a map of project-level dependencies.  
- **Project Lookup Tool:** Add an MCP tool `find_projects_using(shared_lib_name)` that returns all project nodes importing that library (follows both manifest edges and code imports via graph queries).  
- **Project Discovery Tool:** Implement a skill `identify_project(file_or_keyword)` that scans manifests or uses project naming heuristics to map context to a project. (This can be a lightweight Python function rather than an LLM.)  
- **Bounded search filters:** Integrate support for filtering `search_semantic` or `search_bm25` by project (using CodeNib’s index, which supports path-based queries). This might involve passing a path prefix or patching the search indexer to tag view manifests.  

**Phase 3: Skill Development** (Weeks 5–7)  
- **Project/Workspace Skill:** Code an agent tool that uses the above discovery tool. E.g. if task mentions “library X”, run `find_projects_using(X)` to pinpoint workspace context.  
- **Scope-Limited Search Skill:** Implement an LLM prompt or policy that takes the project ID and narrows any search queries to that subtree. This may involve either pre-filtering the query or post-filtering results from CodeNib (filter results by path prefix).  
- **Dependency Navigator Skill:** Build or script an LLM tool: given a symbol or module name, it calls `dependency_subgraph` or our new `find_projects_using`. Example: user says “show who calls AuthProvider”, the agent uses `dependency_subgraph` or `routes` with that symbol to get references.  
- **Call-Graph Expansion Skill:** Integrate a traversal service. One approach: reuse CodeNib’s igraph interface (maybe via Python) to perform BFS/DFS on call edges up to a depth, returning all reachable symbols and their code. Provide this as an MCP tool or sub-skill.  
- **Test Locator/Build Context Skill:** Create a script that parses typical config (e.g. `jest.config.js`, CI YAML, `Cargo.toml [dev-dependencies]`) to link code modules to test targets. The agent can call it to get test files or commands related to a project.  

Deliverables by end of Phase 3: a set of CLI/HTTP services or MCP endpoints for each skill. For example, a JSON API `{"action":"find_callers","symbol":"AuthProvider"}` returns caller file locations; or a filterable search command; or a project identifier function.  

**Phase 4: Context Planner & Orchestration** (Weeks 8–10)  
- **Planner Design:** Define an algorithm or LLM prompt that, given a task description and current context, chooses next steps. For example:  
  1. Use `Project Discovery` to focus scope.  
  2. Run `Scope-Limited Search` for task keywords. If results insufficient, then:  
  3. Query `Dependency Navigator` on key symbols from step 2.  
  4. `Call-Graph Expansion` on any interface functions found.  
  5. Retrieve code blocks with `explore_context` as needed.  
  6. Loop or stop when test coverage or design info looks complete.  
- **Budget Enforcement:** The planner tracks token usage (sum of context given to LLM) and time taken. It should have thresholds to stop. For example, “stop if added context > 2048 tokens or no high-confidence findings in last 3 steps.”  
- **Implementation:** Likely best implemented as an LLM-based sub-agent prompt (e.g. using a custom RAG pipeline) or as rule-based logic wrapping LLM calls. Use CodeNib’s context API (`explore_context`) with explicit `token_budget` hints if supported.  
- **Integration:** The planner can be a “meta-skill” the main agent calls. We might implement it as a Python function that interacts with the MCP server, or an LLM function using context.  

Deliverables: a working context-planner component. For example, a sequence diagram or pseudocode. We should produce pseudocode illustrating loop with `if/else` steps and tool calls (ensuring brevity per user instructions).

**Phase 5: Agent Integration & Testing** (Weeks 11–12)  
- **Agent Hook:** Integrate the new tools and planner with the coding agent (e.g. via MCP or CLI wrapper). Ensure the agent invokes our tools rather than blind search. Possibly script a prompt that instructs the agent to use these tools (“Use project_aware_search, dependency_subgraph, etc.”).  
- **Example Scenario:** Pick a real open-source monorepo (e.g. a known Node monorepo or Rust workspace) and a concrete feature request or bug. Demonstrate: task → planner → tools → code change → tests.  
- **Refinement:** Evaluate token/time used vs baseline. Adjust planner heuristics.  

Deliverables: an end-to-end demo notebook or script showing a feature addition flow using our system. Also documentation of the APIs (tool endpoints) and their inputs/outputs.  

# Key Deliverables & Pitfalls  
- **Deliverable: Extended CodeNib indexer.** Modified CodeNib view compiler that includes workspace/project nodes in `graph.pkl`. Unit tests should verify that parsing a sample manifest tree yields correct nodes/edges. Avoid duplicating indexes (don’t build a second DB).  
- **Deliverable: New MCP tools.** Extended `dependency_subgraph` with project mode; new tools like `find_projects_using()`, `identify_project()`, etc. Document their inputs (e.g. symbol name) and outputs (list of file paths or project IDs).  
- **Deliverable: Skills code.** Implement project discovery, scope-limited search, call-graph, test-locator as Python scripts or MCP skills. Provide examples of use. Ensure each is reversible or idempotent (safe to call repeatedly).  
- **Deliverable: Context Planner.** Pseudocode or code for the orchestration algorithm (including scoring functions or decision logic). Possibly implement as part of the agent prompt sequence.  
- **Deliverable: Evaluation Plan.** Prepare a testing harness comparing: (A) raw agent, (B) agent+static CodeNib tools, (C) agent+our planner. Use metrics: **success (tests passed)**, **tokens used**, **time**, **context precision** (useful vs retrieved context). Use a few tasks from existing repo-level benchmarks (SWE-bench, ContextBench) for realism.  

**Key Gotchas:**  
- Don’t rebuild the entire CodeNib stack (reuse BM25, SCIP, igraph).  
- Avoid Python-only assumptions: design graph ingestion to support multiple languages (manifest scanning logic can be language-neutral by file patterns).  
- Watch token bloat: use bounded reads (`explore_context` retrieves only specific blocks).  
- Graph updates: ensure incremental indexing handles adding project nodes (should follow the same diff logic). CodeNib’s diff-based repair should already handle adding files; adding manifest nodes may require treating manifest files as files and linking after.  
- Tools integration: avoid forcing the agent to call too many tools (limit via planner).  

**What *not* to build:** A new LLM or agent. A separate graph DB. A full evaluation benchmark. (Cite JetBrains blog: agents already need context; context retrieval is established as needed, so novelty is in orchestration.)  

# Evaluation Metrics and Scheme  
Use a lightweight yet targeted evaluation:  
- **Task Success:** whether code changes pass tests / meet requirements (primary objective).  
- **Token Cost:** total LLM tokens consumed per task (prompt+completion), measured from agent logs.  
- **Latency:** wall-clock time for task (including tool calls).  
- **Context Efficiency:** fraction of retrieved context that was used by the agent (as per ContextBench notions). For example, track how much of the fetched code was relevant.  
- **Change Quality:** measure excess changes: lines/files changed beyond what’s needed. Also count test failures/regressions.  

Compare these metrics across strategies: (A) no repo intelligence, (B) existing static search (CodeNib unmodified), (C) our orchestrated system. Keep tasks constant (from a selected repo, possibly drawn from SWE-Bench) and use the same agent model for fairness.  

Finally, produce a detailed architecture diagram and API spec in documentation, but the immediate research deliverable is this stepwise plan and skill design. The next session will refine classes/interfaces and final design.

**References:** This plan builds on CodeNib’s multi-view indexing and on findings from Repo Mind and ContextBench about holistic repo context. It incorporates graph-guided localization from LocAgent and change-impact planning from CodePlan. The overarching goal is to **maximize task success per token/time** by dynamic context orchestration, not by dumping entire repos into prompts.
# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""CodeNib MCP server - stdio transport.

Exposes vector (semantic), BM25, regex, and Zoekt trigram search over a
pre-built CodeNib index via the Model Context Protocol.

Usage::

    codenib-mcp --manifest /path/to/repo_manifest.json

Or as a module::

    python -m codenib.mcp.server --manifest /path/to/repo_manifest.json
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Optional

from mcp.types import ToolAnnotations
from pydantic import Field

from ..compiler.manifest import RepoManifest
from ..log_utils import set_console_log_level
from .context import ServerContext
from .explore_session import ExploreSessionLedger, ExploreSessionRuntime
from .prompts import (
    CODENIB_EXPLORE_GUIDE,
    CODENIB_EXPLORE_INSTRUCTIONS,
    CODENIB_FULL_INSTRUCTIONS,
    CODENIB_GUIDE,
)
from .schemas import ExploreResponse
from .tool_surface import (
    TOOL_SURFACE_EXPLORE,
    TOOL_SURFACE_FULL,
    TOOL_SURFACES,
    ToolSurfaceMCPServer,
)
from .tools._validation import (
    MAX_DEPENDENCY_EDGES,
    MAX_EXPLORE_WINDOWS,
    MAX_GRAPH_DEPTH,
    MAX_LSP_POSITION,
    MAX_LSP_QUERY_CHARS,
    MAX_LSP_SYMBOL_CHARS,
    MAX_REGEX_FILTER_CHARS,
    MAX_REGEX_PATTERN_CHARS,
    MAX_ROUTE_SYMBOLS,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SOURCE_PATH_CHARS,
    MAX_TOOL_RESULTS,
)
from .tools.dependency import dependency_subgraph_impl, find_projects_using_impl
from .tools.explore import explore_context_impl
from .tools.lsp import lsp_definition_impl, lsp_references_impl, lsp_route_impl
from .tools.search import search_bm25_impl, search_context_impl, search_regex_impl
from .tools.search import search_semantic as _search_semantic_impl
from .tools.search import search_zoekt_impl
from .tools.source import read_source_impl

if TYPE_CHECKING:
    from ..artifacts.runtime import ContextArtifactBinding
    from ..source_fingerprint import RepositorySourceBinding

logger = logging.getLogger(__name__)

# Historical embedders import ``MCPServer`` from this module. Keep that alias
# while specializing the server with opt-in tool visibility.
MCPServer = ToolSurfaceMCPServer

# Global context is set once at startup before the event loop runs tools.
_ctx: Optional[ServerContext] = None
_SearchText = Annotated[
    str,
    Field(
        min_length=1,
        description="A non-empty symbol or identifier seed resolved by CodeNib.",
    ),
]
_SearchQuery = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_SEARCH_QUERY_CHARS,
        description=(
            "A repository question, natural-language code query, exact name, "
            "or error string."
        ),
    ),
]
_SearchTopK = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_TOOL_RESULTS,
        description="Maximum number of ranked locations or graph nodes to return.",
    ),
]
_RegexPattern = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_REGEX_PATTERN_CHARS,
        description="A Python-compatible regex pattern for CodeGraph nodes.",
    ),
]
_RegexFilter = Annotated[
    str,
    Field(
        max_length=MAX_REGEX_FILTER_CHARS,
        description="Optional file glob or node-type filter that narrows matches.",
    ),
]
_SearchLevel = Annotated[
    Literal["l0", "l2"],
    Field(description="Search granularity: l0 files or l2 symbols/functions."),
]
_FiniteScore = Annotated[
    float,
    Field(
        allow_inf_nan=False,
        description="Optional finite similarity threshold; zero disables filtering.",
    ),
]
_GraphDirection = Annotated[
    Literal["impact", "dependencies", "both"],
    Field(
        description=(
            "Graph direction: impact callers, dependencies callees, or both "
            "for a local neighborhood."
        )
    ),
]
_GraphDepth = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_GRAPH_DEPTH,
        description="Maximum graph traversal depth; increase only for a named gap.",
    ),
]
_DependencyEdges = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_DEPENDENCY_EDGES,
        description="Independent maximum number of graph relationships to return.",
    ),
]
_PositiveLine = Annotated[
    int,
    Field(ge=1, le=MAX_LSP_POSITION, description="One-based source line."),
]
_Character = Annotated[
    int,
    Field(ge=0, le=MAX_LSP_POSITION, description="Zero-based character offset."),
]
_LspFilePath = Annotated[
    str,
    Field(
        max_length=MAX_SOURCE_PATH_CHARS,
        description="Repository-relative POSIX path used for LSP location lookup.",
    ),
]
_LspSymbol = Annotated[
    str,
    Field(
        max_length=MAX_LSP_SYMBOL_CHARS,
        description="Optional symbol seed used for static navigation.",
    ),
]
_LspQuery = Annotated[
    str,
    Field(
        max_length=MAX_LSP_QUERY_CHARS,
        description="Fallback route query used when reliable symbol seeds are absent.",
    ),
]
_RouteSymbols = Annotated[
    list[_LspSymbol],
    Field(
        max_length=MAX_ROUTE_SYMBOLS,
        description="Known symbol seeds for compact route navigation.",
    ),
]
_SourcePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_SOURCE_PATH_CHARS,
        description="Authenticated repository-relative POSIX source path.",
    ),
]
_ExploreFilePath = Annotated[
    str,
    Field(
        max_length=MAX_SOURCE_PATH_CHARS,
        description="Optional repository-relative path used to resolve project scope.",
    ),
]
_ExploreTopK = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_EXPLORE_WINDOWS,
        description="Maximum admitted source windows in composed exploration.",
    ),
]
_ExploreBudget = Annotated[
    Literal["fast", "balanced", "thorough"],
    Field(
        description=(
            "Retrieval budget: fast for orientation, balanced for normal work, "
            "or thorough when more relationships are required."
        )
    ),
]
_RetrievalBudget = Annotated[
    str,
    Field(
        description=(
            "Retrieval budget: fast, balanced, or thorough. The implementation "
            "also accepts legacy aliases and normalizes them."
        )
    ),
]
_ProjectId = Annotated[
    str,
    Field(
        description=(
            "Optional exact project ID. Use it to keep retrieval within a known "
            "project; do not invent one."
        )
    ),
]
_FilterTest = Annotated[
    bool,
    Field(description="When true, exclude test-file results from retrieval."),
]
_IncludeDependencies = Annotated[
    bool,
    Field(description="Include bounded dependency relationships in exploration."),
]
_IncludeDeclaration = Annotated[
    bool,
    Field(description="Include the declaration when returning static references."),
]
_IncludeNeighbors = Annotated[
    bool,
    Field(description="Include neighboring route anchors around LSP seeds."),
]
_CaseSensitive = Annotated[
    bool,
    Field(description="Match regex with case sensitivity when true."),
]
_GraphGranularity = Annotated[
    Literal["symbol", "project"],
    Field(
        description=(
            "Return symbol-level edges or aggregate the dependency graph at "
            "project level."
        )
    ),
]
_FileFilter = Annotated[
    str,
    Field(
        description=(
            "Optional Zoekt file glob/regex filter, useful for project, language, "
            "test, or configuration scope."
        )
    ),
]

_READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@asynccontextmanager
async def _mcp_lifespan(
    _server: Any,
) -> AsyncIterator[ExploreSessionRuntime | None]:
    """Own one fresh exploration runtime for each transport connection."""

    ctx = _ctx
    runtime = ctx.begin_explore_session() if ctx is not None else None
    try:
        yield runtime
    finally:
        if ctx is not None and runtime is not None:
            ctx.end_explore_session(runtime)


def get_context() -> ServerContext:
    """Return the loaded ServerContext or raise if uninitialized."""
    if _ctx is None:
        raise RuntimeError("ServerContext not initialized. Call init_server() first.")
    _ctx.reload_if_stale()
    return _ctx


# MCPServer configures the process-wide root logger while it is constructed.
# Keep imports safe for embedding applications; `main()` configures logging
# explicitly after parsing the requested CLI level.
_root_logger = logging.getLogger()
_import_logging_guard: logging.NullHandler | None = None
if not _root_logger.handlers:
    _import_logging_guard = logging.NullHandler()
    _root_logger.addHandler(_import_logging_guard)
try:
    mcp = MCPServer(
        "codenib",
        instructions=CODENIB_FULL_INSTRUCTIONS,
        lifespan=_mcp_lifespan,
    )
finally:
    if _import_logging_guard is not None:
        _root_logger.removeHandler(_import_logging_guard)


def configure_tool_surface(value: str) -> str:
    """Apply one surface consistently to discovery, calls, and instructions."""

    surface = mcp.set_tool_surface(value)
    mcp._lowlevel_server.instructions = (  # noqa: SLF001 - SDK has no setter
        CODENIB_EXPLORE_INSTRUCTIONS
        if surface == TOOL_SURFACE_EXPLORE
        else CODENIB_FULL_INSTRUCTIONS
    )
    return surface


def _finish_abandoned_explore_worker(
    runtime: ExploreSessionRuntime,
    worker: asyncio.Task[Any],
) -> None:
    """Consume a cancelled caller's worker and release its runtime barrier."""

    if runtime.pending_worker is worker:
        runtime.pending_worker = None
    if not worker.cancelled():
        worker.exception()


async def _wait_for_abandoned_explore_worker(
    runtime: ExploreSessionRuntime,
) -> None:
    """Keep backend access serialized after an earlier caller cancelled."""

    worker = runtime.pending_worker
    if worker is None:
        return
    try:
        await asyncio.shield(worker)
    except Exception:  # noqa: BLE001 - the abandoned call was already cancelled
        pass
    finally:
        if worker.done() and runtime.pending_worker is worker:
            runtime.pending_worker = None


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------


@mcp.tool(
    name="explore_context",
    description=(
        "Recommended first call for repository context planning. Composes ranked "
        "retrieval, LSP-shaped routing, bounded dependency expansion, and verified "
        "source windows. Use budget='fast' for orientation, 'balanced' for normal "
        "investigation, or 'thorough' when the first result lacks relationships. "
        "Pass an exact project_id or repository-relative file_path when scope is "
        "known; unresolved explicit scope blocks retrieval. Add symbols for known "
        "entry points, direction='impact' for callers or blast radius, "
        "direction='dependencies' for callees, and direction='both' for a local "
        "neighborhood. Set filter_test=true for implementation-only retrieval. "
        "Returns the concrete provider plan, scope, project context, source "
        "identity, relationships, diagnostics, and per-connection delivery usage."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
    structured_output=True,
)
async def explore_context(
    query: _SearchQuery,
    symbols: _RouteSymbols | None = None,
    top_k: _ExploreTopK = 8,
    budget: _ExploreBudget = "balanced",
    direction: _GraphDirection = "both",
    include_dependencies: _IncludeDependencies = True,
    filter_test: _FilterTest = False,
    project_id: _ProjectId = "",
    file_path: _ExploreFilePath = "",
) -> ExploreResponse:
    """Compose repository context and commit delivery history atomically."""

    ctx = get_context()
    runtime = ctx.ensure_explore_session()
    async with runtime.call_lock:
        await _wait_for_abandoned_explore_worker(runtime)
        worker = asyncio.create_task(
            asyncio.to_thread(
                explore_context_impl,
                ctx,
                query,
                symbols or (),
                top_k,
                budget,
                direction,
                include_dependencies,
                filter_test,
                project_id or None,
                file_path,
            )
        )
        try:
            response = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # ``to_thread`` cannot stop an already-running function. Let it
            # finish read-only and never commit it. Cancellation remains
            # responsive; the next call waits on this worker before touching
            # the same providers.
            runtime.pending_worker = worker
            worker.add_done_callback(
                lambda completed: _finish_abandoned_explore_worker(
                    runtime,
                    completed,
                )
            )
            raise
        validated = ExploreResponse.model_validate(runtime.ledger.project(response))
        # Return a plain mapping for legacy in-process callers while the
        # annotation supplies the MCP structured-output schema.
        return validated.model_dump()


@mcp.tool(
    name="search_context",
    description=(
        "Recommended lower-level ranked search when one composed exploration call "
        "is not enough or a route must be inspected explicitly. CodeNib selects "
        "a deterministic BM25, dense, hybrid-RRF, or graph-expanded route. Use "
        "budget='fast' for a small orientation search, 'balanced' for normal "
        "work, and 'thorough' when more candidates or graph expansion are needed. "
        "Use level='l0' for file-level conceptual retrieval or level='l2' for "
        "symbols/functions; set filter_test=true to exclude test files and pass "
        "project_id for project-scoped retrieval. Returns the selected plan, "
        "repository/source provenance, diagnostics, and source-linked results."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def search_context(
    query: _SearchQuery,
    top_k: _SearchTopK = 10,
    budget: _RetrievalBudget = "balanced",
    level: _SearchLevel = "l2",
    filter_test: _FilterTest = False,
    project_id: _ProjectId = "",
) -> dict[str, Any]:
    """Execute capability-aware ranked retrieval over loaded repository views."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        search_context_impl,
        _ctx,
        query,
        top_k,
        budget,
        level,
        filter_test,
        project_id or None,
    )


@mcp.tool(
    name="search_semantic",
    description=(
        "Force vector-semantic retrieval for conceptual or natural-language "
        "questions when exact identifiers are unknown. Use level='l0' to find "
        "relevant files or level='l2' for functions/classes/methods; use "
        "score_threshold only to suppress weak matches and pass project_id when "
        "scope is known. Results are candidates, not proof; bind important hits "
        "with LSP and inspect them with read_source. Returns an explicit error "
        "when the vector view is unavailable."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def semantic_search(
    query: _SearchQuery,
    top_k: _SearchTopK = 10,
    level: _SearchLevel = "l2",
    score_threshold: _FiniteScore = 0.0,
    project_id: _ProjectId = "",
) -> list[dict[str, Any]] | dict[str, str]:
    """Semantic search over indexed code using vector embeddings.

    Returns a list of code-node dicts; on missing vector index, returns
    ``{"error": ...}`` so callers can recover gracefully.
    """
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await _search_semantic_impl(
        ctx=_ctx,
        query=query,
        top_k=top_k,
        level=level if level else "l2",
        score_threshold=score_threshold if score_threshold > 0 else None,
        project_id=project_id or None,
    )


@mcp.tool(
    name="search_bm25",
    description=(
        "Use BM25 for exact names, identifiers, error strings, or keyword-heavy "
        "queries. It returns ranked symbol candidates with projected source "
        "content. Use filter_test=true to exclude test files and project_id to "
        "keep results within a known project. Prefer search_regex for structural "
        "patterns and search_zoekt for comments, docs, configuration, or other "
        "raw text; verify important candidates with LSP/read_source."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def search_bm25(
    query: _SearchQuery,
    top_k: _SearchTopK = 20,
    filter_test: _FilterTest = False,
    project_id: _ProjectId = "",
) -> list[dict[str, Any]]:
    """BM25 keyword search over indexed code symbols."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        search_bm25_impl, _ctx, query, top_k, filter_test, project_id or None
    )


@mcp.tool(
    name="search_regex",
    description=(
        "Use CodeGraph regex search for structural patterns across file and symbol "
        "nodes: test names, decorators, TODOs, class/function shapes, or node-type "
        "constrained matches. Pass file_glob to narrow a resolved project or test "
        "tree and node_type to distinguish file, class, function, or method nodes. "
        "Use case_sensitive only when pattern spelling matters. Prefer BM25 for an "
        "exact identifier and Zoekt for raw comments/docs/configuration. Results "
        "are graph candidates and should be followed by read_source."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def search_regex(
    pattern: _RegexPattern,
    top_k: _SearchTopK = 20,
    file_glob: _RegexFilter = "",
    node_type: _RegexFilter = "",
    case_sensitive: _CaseSensitive = False,
) -> list[dict[str, Any]]:
    """Regex pattern search over code graph nodes."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        search_regex_impl,
        _ctx,
        pattern,
        top_k,
        file_glob or None,
        node_type or None,
        case_sensitive,
    )


@mcp.tool(
    name="search_zoekt",
    description=(
        "Use the Zoekt trigram index for fast raw-text lookups that may occur in "
        "comments, documentation, configuration, generated-looking text, or files "
        "outside CodeGraph. The query may be a substring, regex:pattern, or include "
        "atoms such as case:no and lang:python. Use file_filter to narrow by project, "
        "language, test tree, or configuration path. Results are file-level matches "
        "with line ranges; call read_source before treating a match as evidence. "
        "Prefer BM25 for ranked symbols and search_regex for structural graph nodes."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def search_zoekt(
    query: _SearchQuery,
    top_k: _SearchTopK = 20,
    file_filter: _FileFilter = "",
) -> list[dict[str, Any]]:
    """Trigram-based search over raw repository contents."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        search_zoekt_impl,
        _ctx,
        query,
        top_k,
        file_filter or None,
    )


@mcp.tool(
    name="dependency_subgraph",
    description=(
        "Use the bounded graph for structural questions that search cannot answer. "
        "direction='impact' follows transitive callers for change blast radius; "
        "direction='dependencies' follows transitive callees; direction='both' "
        "returns a local caller/callee neighborhood. Use granularity='symbol' for "
        "function-level edges or granularity='project' for aggregated project "
        "relationships. Increase depth, max_nodes, or max_edges only for a named "
        "evidence gap. Resolve fuzzy symbols with BM25/LSP first when possible; "
        "unresolved names return a note and must not become guessed edges."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def dependency_subgraph(
    symbol: _SearchText,
    direction: _GraphDirection = "both",
    depth: _GraphDepth = 2,
    max_nodes: _SearchTopK = 60,
    max_edges: _DependencyEdges = 400,
    granularity: _GraphGranularity = "symbol",
) -> dict[str, Any]:
    """Call-graph subgraph for *symbol*, bounded by node and edge budgets."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        dependency_subgraph_impl,
        _ctx,
        symbol,
        direction,
        depth,
        max_nodes,
        max_edges,
        granularity,
    )


@mcp.tool(
    name="find_projects_using",
    description=(
        "Use for cross-project impact of a resolved shared symbol or library. "
        "Returns bounded external workspace projects and supporting evidence; "
        "max_projects limits project rows and max_evidence limits supporting "
        "locations. Resolve the symbol before calling when possible, and follow "
        "returned project IDs with project-scoped search_context or explore_context. "
        "Do not infer consumers from name similarity when the project graph is "
        "unavailable."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def find_projects_using_tool(
    symbol: _SearchText,
    max_projects: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Maximum number of consuming projects to return.",
        ),
    ] = 100,
    max_evidence: Annotated[
        int,
        Field(
            ge=1,
            le=200,
            description="Maximum supporting cross-project evidence rows.",
        ),
    ] = 200,
) -> dict[str, Any]:
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        find_projects_using_impl,
        _ctx,
        symbol,
        max_projects,
        max_evidence,
    )


@mcp.tool(
    name="lsp_definition",
    description=(
        "Use static navigation to bind a candidate symbol or source position to "
        "definition locations. Provide either symbol or repository-relative "
        "file_path plus a 1-based line and optional 0-based character. Results "
        "are compact locations from the runtime LSP provider or persisted graph "
        "fallback, not source evidence; call read_source on selected locations "
        "before finalizing a claim. Inspect provider/fallback metadata when the "
        "result is incomplete."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def lsp_definition(
    file_path: _LspFilePath = "",
    line: _PositiveLine | None = None,
    character: _Character | None = None,
    symbol: _LspSymbol = "",
    top_k: _SearchTopK = 8,
) -> list[dict[str, Any]] | dict[str, str]:
    """Provider-backed definition lookup with persisted-graph fallback."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        lsp_definition_impl,
        _ctx,
        file_path,
        line,
        character,
        symbol,
        top_k,
    )


@mcp.tool(
    name="lsp_references",
    description=(
        "Use static navigation to find direct references or declarations for a "
        "resolved symbol or source position. Provide either symbol or a "
        "repository-relative file_path with 1-based line and optional 0-based "
        "character. Set include_declaration=false when the declaration is already "
        "known. Results are locations only; use read_source for selected callers "
        "and dependency_subgraph for transitive impact. Static references do not "
        "prove dynamic or reflective usage."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def lsp_references(
    file_path: _LspFilePath = "",
    line: _PositiveLine | None = None,
    character: _Character | None = None,
    symbol: _LspSymbol = "",
    include_declaration: _IncludeDeclaration = True,
    top_k: _SearchTopK = 40,
) -> list[dict[str, Any]] | dict[str, str]:
    """Provider-backed reference lookup with persisted-graph fallback."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        lsp_references_impl,
        _ctx,
        file_path,
        line,
        character,
        symbol,
        include_declaration,
        top_k,
    )


@mcp.tool(
    name="lsp_route",
    description=(
        "Use compact LSP-shaped route navigation for endpoint, bridge/factory, "
        "provider/value, or type anchors. Pass known symbols, or pass symbols=[] "
        "with a non-blank query when no reliable symbol is known. Set "
        "include_neighbors=false for only the supplied route seeds. Results are "
        "locations from the runtime provider or graph fallback; bind important "
        "anchors with lsp_definition and read_source before finalizing. Direct "
        "calls have no project_id filter, so use explore_context when strict "
        "project isolation is load-bearing."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def lsp_route(
    symbols: _RouteSymbols,
    query: _LspQuery = "",
    top_k: _SearchTopK = 12,
    include_neighbors: _IncludeNeighbors = True,
) -> list[dict[str, Any]] | dict[str, str]:
    """Provider-backed route map with persisted-graph fallback."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        lsp_route_impl,
        _ctx,
        symbols,
        query,
        top_k,
        include_neighbors,
    )


@mcp.tool(
    name="read_source",
    description=(
        "Read a bounded source window after search or static navigation identifies "
        "an exact location. Use only a repository-relative POSIX path and a 1-based "
        "inclusive line range of at most 200 lines; responses contain at most "
        "16,000 source characters. The server reads only while the retained source "
        "binding is verified and returns its fingerprint/provenance. Use this tool "
        "before finalizing source-backed claims; if the binding is unavailable, keep "
        "indexed excerpts explicitly marked as unverified instead."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def read_source(
    file_path: _SourcePath,
    start_line: _PositiveLine = 1,
    end_line: _PositiveLine | None = None,
) -> dict[str, Any]:
    """Return one bounded window from content-authenticated source bytes."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    return await asyncio.to_thread(
        read_source_impl,
        _ctx,
        file_path,
        start_line,
        end_line,
    )


@mcp.tool(
    name="get_manifest",
    description=(
        "Call once at the start of an MCP connection or whenever capability state "
        "is uncertain. Returns repository path/commit/languages, loaded views, "
        "view errors, workspace/project-query and project-retrieval availability, "
        "LSP provider selection, source verification and provenance, tool_surface, and session "
        "usage. Use it to choose valid search/LSP/graph fallbacks; it does not "
        "return source context itself."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
async def get_manifest() -> dict[str, Any]:
    """Return the repo manifest as a dict."""
    if _ctx is None:
        raise RuntimeError("Server not initialized")
    _ctx.reload_if_stale()
    source_verified = _ctx.verify_source_status()
    result = _ctx.manifest.to_dict()
    workspace = dict(_ctx.workspace_status)
    if workspace.get("status") == "available":
        workspace.setdefault("project_queries", "available")
        # Python is the correctness path in Phase 1b; the existing native
        # provider has no digest-verified overlay merge boundary yet.
        workspace.setdefault("native_project_queries", "unavailable")
        bm25_available = bool(getattr(_ctx.bm25, "project_filter_available", False))
        vector_available = (
            any(_ctx.vector.project_filter_available(level) for level in ("l0", "l2"))
            if _ctx.vector is not None
            else False
        )
        retrieval_available = bm25_available or vector_available
        workspace.setdefault(
            "project_retrieval",
            "available" if retrieval_available else "unavailable",
        )
    result["workspace"] = workspace
    explore_runtime = getattr(_ctx, "explore_runtime", None)
    explore_session = (
        explore_runtime.ledger.stats()
        if isinstance(explore_runtime, ExploreSessionRuntime)
        else ExploreSessionLedger().stats()
    )
    result["runtime"] = {
        "loaded_views": sorted(_ctx.loaded_views),
        "view_errors": dict(sorted(_ctx.errors.items())),
        "tool_surface": mcp.tool_surface,
        "explore_session": explore_session,
        "source_read": {
            "verified": source_verified,
            "error": _ctx.source_error,
            "verification_scope": _ctx.source_verification_scope,
            "commit_verified": False,
            "checkout_state": "not-attested",
        },
        "lsp_provider": dict(_ctx.lsp_provider_selection),
    }
    if _ctx.artifact is not None:
        result["artifact"] = dict(_ctx.artifact)
    return result


# ------------------------------------------------------------------
# Prompt resource
# ------------------------------------------------------------------


@mcp.prompt(
    name="codenib-guide",
    description="Guidance on how to use CodeNib search tools effectively.",
)
async def codenib_guide() -> str:
    if mcp.tool_surface == TOOL_SURFACE_EXPLORE:
        return CODENIB_EXPLORE_GUIDE
    return CODENIB_GUIDE


# ------------------------------------------------------------------
# Status (non-tool helper, used by tests and debugging)
# ------------------------------------------------------------------


def server_status() -> str:
    """Get server status and loaded indexes as readable text."""
    try:
        ctx = get_context()
        lines = [
            f"Repo: {ctx.manifest.repo_path}",
            f"Commit: {(ctx.manifest.commit or '')[:8]}",
            f"Languages: {', '.join(ctx.manifest.languages)}",
            "",
            "Indexes:",
        ]

        if ctx.vector is not None:
            stats = ctx.vector.get_stats()
            lines.append(
                f"  ✓ vector: {ctx.vector.embedding_model} "
                f"({stats['total_documents']} docs)"
            )
        else:
            lines.append("  ✗ vector: not_loaded")

        if ctx.bm25 is not None:
            lines.append("  ✓ bm25: loaded")
        else:
            lines.append("  ✗ bm25: not_loaded")

        if ctx.symbol_graph is not None:
            lines.append("  ✓ symbol_graph: loaded")
        else:
            lines.append("  ✗ symbol_graph: not_loaded")

        lsp_selection = ctx.lsp_provider_selection
        lines.append(
            "  lsp_provider: "
            f"{lsp_selection.get('backend', 'unavailable')} "
            f"({lsp_selection.get('status', 'unavailable')})"
        )

        if ctx.zoekt is not None:
            lines.append(f"  ✓ zoekt: port={ctx.zoekt.port}")
        else:
            lines.append("  ✗ zoekt: not_loaded")

        source_verified = ctx.verify_source_status()
        source_status = (
            "verified(content-bytes; commit=not-attested)"
            if source_verified
            else (ctx.source_error or "unverified")
        )
        lines.append(f"  source_read: {source_status}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting status: {e}"


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

_CLI_NAMES = frozenset({"codenib-mcp"})


def _cli_program_name() -> str:
    invoked_name = Path(sys.argv[0]).name
    if invoked_name in _CLI_NAMES:
        return invoked_name
    return "codenib-mcp"


def _parse_args(
    argv: list[str] | None = None,
    *,
    prog: str | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=prog or _cli_program_name(),
        description="Start the CodeNib MCP server (stdio transport).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=str,
        help="Path to repo_manifest.json produced by IndexCompiler.",
    )
    parser.add_argument(
        "--manifest",
        dest="manifest_flag",
        type=str,
        help="Path to repo_manifest.json produced by IndexCompiler.",
    )
    parser.add_argument(
        "--artifact",
        type=str,
        help="Verified portable context artifact directory.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        help="Exact repository checkout bound to --artifact.",
    )
    parser.add_argument(
        "--repository",
        type=str,
        help="Expected owner/repository identity for --artifact.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    parser.add_argument(
        "--tool-surface",
        choices=sorted(TOOL_SURFACES),
        default=TOOL_SURFACE_FULL,
        help="MCP tool discovery and call surface.",
    )
    return parser.parse_args(argv)


def init_server(
    manifest_path: RepoManifest | str | Path,
    *,
    artifact: dict[str, Any] | None = None,
    artifact_binding: ContextArtifactBinding | None = None,
    source_binding: RepositorySourceBinding | None = None,
) -> None:
    """Initialize the global ServerContext from a manifest file.

    Loads the manifest and opens all available indexes in the
    module-level ``_ctx``. Safe to call from tests with a temporary
    manifest path.

    Raises:
        FileNotFoundError: if ``manifest_path`` does not exist.
    """
    global _ctx
    resolved_manifest_path: Path | None = None
    if isinstance(manifest_path, RepoManifest):
        manifest = manifest_path
        logger.info(
            "Loading in-memory manifest for %s@%s",
            manifest.repo_path,
            (manifest.commit or "")[:12],
        )
        compiler_lock = nullcontext()
    else:
        resolved = Path(manifest_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Manifest not found: {resolved}")
        logger.info("Loading manifest from %s", resolved)
        resolved_manifest_path = resolved
        # A local manifest is an explicit administrator boundary. Serialize
        # capture and native loading with the same cache lock as IndexCompiler.
        # Artifact bindings intentionally skip this branch and remain inert.
        from filelock import FileLock

        compiler_lock = FileLock(str(resolved.parent / ".index-compiler.lock"))

    from ..artifacts.runtime import (
        SourceBindingCleanupOwner,
        _raise_source_cleanup_failure,
        _source_cleanup_owner_is_pending,
    )

    capture_cleanup_owner: SourceBindingCleanupOwner | None = None
    new_context: ServerContext | None = None

    def close_capture_owner(
        primary: BaseException,
    ) -> tuple[BaseException | None, object | None]:
        """Close the preinstalled owner and identify any retry authority."""

        cleanup_failure: BaseException | None = None
        if capture_cleanup_owner is not None:
            try:
                capture_cleanup_owner.close()
            except BaseException as exc:  # noqa: B036 - caller selects priority
                cleanup_failure = exc
        pending_owner: object | None = None
        if _source_cleanup_owner_is_pending(capture_cleanup_owner):
            pending_owner = capture_cleanup_owner
        nested_owner = getattr(primary, "source_cleanup_owner", None)
        if pending_owner is None and _source_cleanup_owner_is_pending(nested_owner):
            pending_owner = nested_owner
        return cleanup_failure, pending_owner

    try:
        with compiler_lock:
            if resolved_manifest_path is not None:
                manifest = RepoManifest.load(resolved_manifest_path)

            from ..compiler.manifest_source import (
                capture_repository_source_for_manifest,
                require_manifest_source_identity,
            )
            from ..source_fingerprint import is_secure_source_fingerprint_v2

            source_error: str | None = "source binding has not been verified"
            retained_source = (
                artifact_binding.source_binding
                if artifact_binding is not None
                else source_binding
            )
            verified = False
            if retained_source is not None and is_secure_source_fingerprint_v2(
                manifest.source_fingerprint
            ):
                retained_identity = retained_source.authenticated_identity_snapshot()
                require_manifest_source_identity(
                    retained_identity,
                    manifest,
                    label="MCP repository",
                    mismatch_message=(
                        "repository source bytes do not match the indexed content"
                    ),
                )
                verified = True
            native_authorization = None
            if verified:
                source_error = None
            elif resolved_manifest_path is not None:
                from ..source_fingerprint import lexical_repository_path

                repo_path = lexical_repository_path(manifest.repo_path)
                if not is_secure_source_fingerprint_v2(manifest.source_fingerprint):
                    source_error = (
                        "manifest has no trust-eligible source fingerprint v2"
                    )
                else:
                    capture_cleanup_owner = SourceBindingCleanupOwner()
                    try:
                        retained_source = capture_repository_source_for_manifest(
                            repo_path,
                            manifest,
                            exclude_roots=(resolved_manifest_path.parent,),
                            _source_owner=capture_cleanup_owner.retain,
                        )
                        retained_identity = (
                            retained_source.authenticated_identity_snapshot()
                        )
                        require_manifest_source_identity(
                            retained_identity,
                            manifest,
                            label="MCP repository",
                            mismatch_message=(
                                "repository source bytes do not match the indexed content"
                            ),
                        )
                    except BaseException as exc:  # noqa: B036 - preserve primary
                        cleanup_failure, pending_owner = close_capture_owner(exc)
                        retained_source = None
                        if (
                            not isinstance(exc, Exception)
                            or cleanup_failure is not None
                            or pending_owner is not None
                        ):
                            _raise_source_cleanup_failure(
                                exc,
                                cleanup_failure,
                                pending_owner,
                            )
                        source_error = str(exc)
                    else:
                        verified = True
                        source_error = None

                if verified and manifest.index_is_current("vector"):
                    from ..index.embedding.artifact_integrity import (
                        capture_authenticated_vector_view,
                    )
                    from ..native_index_authorization import (
                        _mint_trusted_local_admin_authorization,
                    )

                    entry = manifest.indexes["vector"]
                    with capture_authenticated_vector_view(entry.path) as vector_view:
                        native_authorization = _mint_trusted_local_admin_authorization(
                            vector_view.ownership,
                            view_type="vector",
                            semantic_contract=entry.config,
                            evidence=(
                                "local-admin-cli-intent",
                                "source-content-fingerprint-v2-bound",
                                "captured-vector-tree-subject",
                            ),
                        )

            new_context = ServerContext.load(
                manifest,
                artifact=artifact,
                artifact_binding=artifact_binding,
                native_index_authorization=native_authorization,
                source_binding=retained_source,
            )
            new_context.source_error = source_error
            if resolved_manifest_path is not None and (
                artifact is None and artifact_binding is None
            ):
                new_context._note_manifest_path(resolved_manifest_path)
            portable_artifact = artifact is not None or artifact_binding is not None
            new_context.configure_lsp_provider(
                allow_native=new_context.source_verified and not portable_artifact,
                native_disabled_reason=(
                    "portable_artifact_uses_persisted_graph"
                    if portable_artifact
                    else "local_source_not_verified"
                ),
            )

        if _ctx is not None:
            _ctx.close()
        _ctx = new_context
        if not _ctx.source_verified:
            logger.warning("Source reads disabled: %s", _ctx.source_error)
    except BaseException as primary:  # noqa: B036 - retain capture owner
        # Once the new context is globally reachable it is the stable owner.
        # Before that point, the preinstalled capture owner must close or retain
        # the source across any return-handoff or startup cancellation.
        if not (new_context is not None and _ctx is new_context):
            cleanup_failure: BaseException | None = None
            pending_owner: object | None = None
            context_source = (
                new_context._source_binding if new_context is not None else None
            )
            if new_context is not None:
                try:
                    new_context.close()
                except BaseException as exc:  # noqa: B036 - apply shared priority
                    cleanup_failure = exc
            if _source_cleanup_owner_is_pending(context_source):
                pending_owner = context_source
            if capture_cleanup_owner is not None:
                owner_failure, owner_pending = close_capture_owner(primary)
                if cleanup_failure is None:
                    cleanup_failure = owner_failure
                if pending_owner is None:
                    pending_owner = owner_pending
            if cleanup_failure is not None or pending_owner is not None:
                _raise_source_cleanup_failure(
                    primary,
                    cleanup_failure,
                    pending_owner,
                )
        raise


def main(argv: list[str] | None = None) -> None:
    """Run the ``codenib-mcp`` console entry point."""
    program_name = _cli_program_name()
    args = _parse_args(argv)
    set_console_log_level(args.log_level)
    configure_tool_surface(args.tool_surface)
    manifest_path = args.manifest_flag or args.manifest
    if args.artifact and manifest_path:
        logger.error("Choose either a manifest or --artifact, not both")
        sys.exit(1)
    if not args.artifact and not manifest_path:
        logger.error(
            "No context provided. Use: %s <manifest> or --artifact <dir> "
            "[--repo <dir>]",
            program_name,
        )
        sys.exit(1)

    try:
        if args.artifact:
            if args.repo:
                from ..artifacts import bind_context_artifact

                binding = bind_context_artifact(
                    args.artifact,
                    args.repo,
                    expected_repository=args.repository,
                )
            else:
                from ..artifacts import query_context_artifact

                binding = query_context_artifact(
                    args.artifact,
                    expected_repository=args.repository,
                )
            artifact = binding.artifact
            try:
                init_server(
                    binding.manifest,
                    artifact={
                        "verified": True,
                        "schema": artifact.metadata["schema"],
                        "repository": artifact.repository,
                        "commit": artifact.commit,
                        "views": list(artifact.views),
                    },
                    artifact_binding=binding,
                )
            except BaseException:  # noqa: B036 - preserve startup failure
                try:
                    binding.close()
                except BaseException:  # noqa: B036 - preserve primary failure
                    pass
                raise
            binding.close()
        else:
            init_server(manifest_path)
        logger.info("Starting MCP server on stdio...")
        mcp.run(transport="stdio")
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception as exc:
        logger.error("Failed to start server: %s", exc)
        logger.debug("MCP server startup failure", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

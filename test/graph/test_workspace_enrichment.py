# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from codenib.compiler.artifact_quality import constrain_graph_to_source_selection
from codenib.graph.code_graph import CodeGraph
from codenib.graph.workspace_enrichment import (
    enrich_graph_with_workspace,
    finalize_workspace_result,
    prune_orphaned_architecture,
    validate_workspace_graph,
)
from codenib.repository_source_selection import RepositorySourceSelection


def _node(graph, name):
    return graph.graph.vs[graph.name_to_vertex[name]]


def test_enrichment_persists_four_tiers_and_manifest_edges(tmp_path: Path):
    (tmp_path / "apps/web/src").mkdir(parents=True)
    (tmp_path / "libs/shared/src").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{"private": true, "workspaces": ["apps/*", "libs/*"]}\n',
        encoding="utf-8",
    )
    (tmp_path / "apps/web/package.json").write_text(
        '{"name": "web", "dependencies": {"shared": "workspace:*"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "libs/shared/package.json").write_text(
        '{"name": "shared"}\n',
        encoding="utf-8",
    )
    (tmp_path / "apps/web/src/main.ts").write_text("export function main() {}\n")
    (tmp_path / "libs/shared/src/lib.ts").write_text("export function lib() {}\n")

    graph = CodeGraph(str(tmp_path))
    graph.add_root_node(".")
    graph.add_file_node("apps/web/src/main.ts")
    graph.add_symbol_node("apps/web/src/main::main", 0, 0, 0)
    graph.add_file_node("libs/shared/src/lib.ts")
    graph.add_symbol_node("libs/shared/src/lib::lib", 0, 0, 0)

    result = enrich_graph_with_workspace(
        graph,
        str(tmp_path),
        source_selection=RepositorySourceSelection(),
    )

    assert result.project_count == 3
    assert result.owned_file_count == 2
    assert result.owned_symbol_count == 2
    assert _node(graph, "apps/web/src/main.ts")["project_id"] == "project://apps/web"
    assert (
        _node(graph, "libs/shared/src/lib.ts")["project_id"] == "project://libs/shared"
    )
    assert _node(graph, "project://libs/shared")["is_shared"] is True

    edge_keys = {
        (
            graph.graph.vs[edge.source]["name"],
            graph.graph.vs[edge.target]["name"],
            edge["type"],
        )
        for edge in graph.graph.es
    }
    assert (
        "workspace://root",
        "project://apps/web",
        "contain",
    ) in edge_keys
    assert (
        "project://apps/web",
        "apps/web/src/main.ts",
        "contain",
    ) in edge_keys
    assert (
        "project://apps/web",
        "project://libs/shared",
        "depends_on_manifest",
    ) in edge_keys
    validate_workspace_graph(graph, result.workspace_summary)


def test_enrichment_is_idempotent_and_keeps_empty_projects(tmp_path: Path):
    (tmp_path / "packages/empty").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"]}\n', encoding="utf-8"
    )
    (tmp_path / "packages/empty/package.json").write_text(
        '{"name": "empty"}\n', encoding="utf-8"
    )

    graph = CodeGraph(str(tmp_path))
    graph.add_root_node(".")
    first = enrich_graph_with_workspace(
        graph,
        str(tmp_path),
        source_selection=RepositorySourceSelection(),
    )
    first_edges = graph.graph.ecount()
    second = enrich_graph_with_workspace(
        graph,
        str(tmp_path),
        source_selection=RepositorySourceSelection(),
    )

    assert graph.name_to_vertex.get("project://packages/empty") is not None
    assert _node(graph, "project://packages/empty")["file_count"] == 0
    assert graph.graph.ecount() == first_edges
    assert first.architecture_digest == second.architecture_digest


def test_source_selection_prunes_project_files_but_retains_project_vertices(
    tmp_path: Path,
):
    (tmp_path / "apps/web").mkdir(parents=True)
    (tmp_path / "libs/shared/src").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["apps/*", "libs/*"]}\n', encoding="utf-8"
    )
    (tmp_path / "apps/web/package.json").write_text(
        '{"name": "web"}\n', encoding="utf-8"
    )
    (tmp_path / "libs/shared/package.json").write_text(
        '{"name": "shared"}\n', encoding="utf-8"
    )

    graph = CodeGraph(str(tmp_path))
    graph.add_root_node(".")
    graph.add_file_node("libs/shared/src/lib.ts")
    graph.add_symbol_node("libs/shared/src/lib::lib", 0, 0, 0)
    selection = RepositorySourceSelection(("libs/shared/src",))
    result = enrich_graph_with_workspace(
        graph, str(tmp_path), source_selection=selection
    )

    report = constrain_graph_to_source_selection(graph, selection)
    assert report["excluded_paths_after"] == []
    prune_orphaned_architecture(graph, result.model)
    result = finalize_workspace_result(graph, result)

    assert "project://libs/shared" in graph.name_to_vertex
    assert "workspace://root" in graph.name_to_vertex
    assert "libs/shared/src/lib.ts" not in graph.name_to_vertex
    assert _node(graph, "project://libs/shared")["file_count"] == 0
    validate_workspace_graph(graph, result.workspace_summary)

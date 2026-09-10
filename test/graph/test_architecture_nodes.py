# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from codenib.graph.code_graph import CodeGraph
from codenib.graph.layers import build_graph_layers
from codenib.graph.workspace_enrichment import (
    architecture_digest,
    validate_workspace_graph,
)
from codenib.scip_interface.query_surface import (
    project_query_surface_sha256,
    source_query_surface_sha256,
)
from codenib.types import (
    DEPENDENCY_EDGE_TYPES,
    EDGE_TYPE_CONTAIN,
    EDGE_TYPE_MANIFEST_DEPENDENCY,
    GRAPH_LAYER_ARCHITECTURE,
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    NODE_TYPE_FUNCTION,
    NODE_TYPE_PROJECT,
    NODE_TYPE_WORKSPACE,
    edge_types_for_graph_layer,
    is_architecture_node,
    is_source_node,
)


def _arch_attrs(node_type):
    return {"type": node_type}


def test_add_architecture_vertex_round_trip():
    graph = CodeGraph("repo")
    vertex_id = graph.add_architecture_vertex(
        "project://libs/shared",
        {**_arch_attrs(NODE_TYPE_PROJECT), "project_path": "libs/shared"},
    )
    assert graph.graph.vs[vertex_id]["type"] == NODE_TYPE_PROJECT


def test_add_architecture_vertex_rejects_non_architecture_type():
    graph = CodeGraph("repo")
    with pytest.raises(ValueError):
        graph.add_architecture_vertex("x", _arch_attrs(NODE_TYPE_FILE))


def test_add_architecture_vertex_rejects_file_attribute():
    graph = CodeGraph("repo")
    with pytest.raises(ValueError):
        graph.add_architecture_vertex(
            "project://x", {"type": NODE_TYPE_PROJECT, "file": "x.py"}
        )


def test_add_architecture_vertex_rejects_collision_with_source_vertex():
    graph = CodeGraph("repo")
    graph.add_file_node("libs/shared/x.py")
    with pytest.raises(ValueError):
        graph.add_architecture_vertex(
            "libs/shared/x.py", _arch_attrs(NODE_TYPE_PROJECT)
        )


def test_add_architecture_vertex_allows_architecture_update():
    graph = CodeGraph("repo")
    first = graph.add_architecture_vertex(
        "project://libs/shared", _arch_attrs(NODE_TYPE_PROJECT)
    )
    second = graph.add_architecture_vertex(
        "project://libs/shared",
        {**_arch_attrs(NODE_TYPE_PROJECT), "display_name": "@co/shared"},
    )
    assert first == second
    assert graph.graph.vs[second]["display_name"] == "@co/shared"


def test_file_vertex_id_survives_save_load_round_trip(tmp_path):
    graph = CodeGraph("repo")
    graph.add_file_node("a.py")
    assert graph.file_vertex_id("a.py") is not None
    assert graph.file_vertex_id("missing.py") is None

    path = tmp_path / "graph.pkl"
    graph.save_graph(str(path))
    loaded = CodeGraph.load_graph(str(path))
    assert loaded.file_vertex_id("a.py") == graph.file_vertex_id("a.py")
    assert loaded.workspace_context is None


def test_file_vertex_id_populated_by_merge_from():
    base = CodeGraph("repo")
    base.add_file_node("a.py")
    other = CodeGraph("repo")
    other.add_file_node("b.py")
    base.merge_from(other)
    assert base.file_vertex_id("a.py") is not None
    assert base.file_vertex_id("b.py") is not None


def test_architecture_attributes_and_edges_survive_round_trip(tmp_path):
    graph = CodeGraph("repo")
    graph.add_file_node("src/app.py")
    graph.add_architecture_vertex(
        "workspace://root",
        {"type": NODE_TYPE_WORKSPACE, "workspace_id": "workspace://root"},
    )
    graph.add_architecture_vertex(
        "project://apps/web",
        {
            "type": NODE_TYPE_PROJECT,
            "project_id": "project://apps/web",
            "is_shared": True,
        },
    )
    graph.graph.vs[graph.name_to_vertex["src/app.py"]][
        "project_id"
    ] = "project://apps/web"
    graph.add_architecture_edge(
        "workspace://root", "project://apps/web", EDGE_TYPE_CONTAIN
    )
    graph.add_architecture_edge("project://apps/web", "src/app.py", EDGE_TYPE_CONTAIN)

    path = tmp_path / "graph.pkl"
    graph.save_graph(path)
    loaded = CodeGraph.load_graph(path)

    assert (
        loaded.graph.vs[loaded.name_to_vertex["project://apps/web"]]["is_shared"]
        is True
    )
    validate_workspace_graph(loaded)


def test_manifest_edge_evidence_is_merged_and_survives_round_trip(tmp_path):
    graph = CodeGraph("repo")
    graph.add_architecture_vertex(
        "project://apps/web",
        {"type": NODE_TYPE_PROJECT, "project_id": "project://apps/web"},
    )
    graph.add_architecture_vertex(
        "project://libs/shared",
        {"type": NODE_TYPE_PROJECT, "project_id": "project://libs/shared"},
    )
    first = {
        "declared_name": "@acme/shared",
        "scope": "runtime",
        "specifier": "workspace:^",
        "source_manifest": "apps/web/package.json",
        "resolution": "workspace",
    }
    second = {
        **first,
        "scope": "optional",
        "source_manifest": "apps/web/package.json",
    }
    graph.add_architecture_edge(
        "project://apps/web",
        "project://libs/shared",
        EDGE_TYPE_MANIFEST_DEPENDENCY,
        manifest_evidence=(first,),
    )
    graph.add_architecture_edge(
        "project://apps/web",
        "project://libs/shared",
        EDGE_TYPE_MANIFEST_DEPENDENCY,
        manifest_evidence=(second, first),
    )

    edge = next(iter(graph.graph.es))
    assert len(edge["manifest_evidence"]) == 2
    digest = architecture_digest(graph)
    context = {
        "workspace_id": "workspace://repo",
        "detected_systems": [],
        "complete": True,
        "topology_digest": "topology",
        "metadata_digest": "metadata",
        "architecture_digest": digest,
        "project_count": 2,
        "projects": [],
        "diagnostics": [],
        "query_surface_schema_version": 3,
    }
    path = tmp_path / "graph.pkl"
    graph.save_graph(path, workspace_context=context)
    loaded = CodeGraph.load_graph(path)

    assert loaded.workspace_context == context
    assert loaded.graph.es[0]["manifest_evidence"] == edge["manifest_evidence"]

    with pytest.raises(ValueError, match="missing required fields"):
        graph.save_graph(path, workspace_context={"architecture_digest": digest})


def test_manifest_evidence_is_rejected_on_containment_edges():
    graph = CodeGraph("repo")
    graph.add_architecture_vertex("workspace://root", {"type": NODE_TYPE_WORKSPACE})
    graph.add_architecture_vertex("project://apps/web", {"type": NODE_TYPE_PROJECT})
    with pytest.raises(ValueError, match="only valid"):
        graph.add_architecture_edge(
            "workspace://root",
            "project://apps/web",
            EDGE_TYPE_CONTAIN,
            manifest_evidence=(
                {
                    "declared_name": "x",
                    "scope": "runtime",
                    "specifier": "*",
                    "source_manifest": "package.json",
                    "resolution": "workspace",
                },
            ),
        )


def test_manifest_evidence_changes_project_but_not_source_query_digest():
    graph = CodeGraph("repo")
    graph.add_file_node("src/app.py")
    graph.add_architecture_vertex(
        "workspace://root",
        {
            "type": NODE_TYPE_WORKSPACE,
            "workspace_id": "workspace://root",
            "workspace_path": ".",
        },
    )
    for project_id, path in (
        ("project://apps/web", "apps/web"),
        ("project://libs/shared", "libs/shared"),
    ):
        graph.add_architecture_vertex(
            project_id,
            {
                "type": NODE_TYPE_PROJECT,
                "project_id": project_id,
                "project_path": path,
            },
        )
    graph.add_architecture_edge(
        "project://apps/web",
        "project://libs/shared",
        EDGE_TYPE_MANIFEST_DEPENDENCY,
    )
    before = project_query_surface_sha256(graph)
    source_before = source_query_surface_sha256(graph)
    graph.graph.es[0]["manifest_evidence"] = (
        {
            "declared_name": "@acme/shared",
            "scope": "runtime",
            "specifier": "workspace:^",
            "source_manifest": "apps/web/package.json",
            "resolution": "workspace",
        },
    )
    assert project_query_surface_sha256(graph) != before
    assert source_query_surface_sha256(graph) == source_before


def test_architecture_edge_rejects_missing_endpoints_before_add():
    graph = CodeGraph("repo")
    graph.add_architecture_vertex("workspace://root", {"type": NODE_TYPE_WORKSPACE})
    with pytest.raises(ValueError, match="existing source and target"):
        graph.add_architecture_edge(
            "workspace://root", "project://missing", EDGE_TYPE_CONTAIN
        )


def test_workspace_validation_rejects_anchored_architecture_edges():
    graph = CodeGraph("repo")
    graph.add_file_node("src/app.py")
    graph.add_architecture_vertex("workspace://root", {"type": NODE_TYPE_WORKSPACE})
    graph.add_architecture_vertex("project://apps/web", {"type": NODE_TYPE_PROJECT})
    graph.add_architecture_edge(
        "workspace://root", "project://apps/web", EDGE_TYPE_CONTAIN
    )
    graph._add_edge(
        "project://apps/web",
        "src/app.py",
        EDGE_TYPE_CONTAIN,
        anchor_file="src/app.py",
        anchor_line=1,
    )
    with pytest.raises(ValueError, match="invalid workspace architecture edge"):
        validate_workspace_graph(graph)


def test_architecture_node_predicates():
    assert is_architecture_node(NODE_TYPE_WORKSPACE)
    assert is_architecture_node(NODE_TYPE_PROJECT)
    assert not is_architecture_node(NODE_TYPE_FILE)
    assert is_source_node(NODE_TYPE_FILE)
    assert is_source_node(NODE_TYPE_DIRECTORY)
    assert is_source_node(NODE_TYPE_FUNCTION)
    assert not is_source_node(NODE_TYPE_WORKSPACE)
    assert not is_source_node(NODE_TYPE_PROJECT)


def test_architecture_and_dependency_layers():
    assert edge_types_for_graph_layer(GRAPH_LAYER_ARCHITECTURE) == frozenset(
        {EDGE_TYPE_CONTAIN, EDGE_TYPE_MANIFEST_DEPENDENCY}
    )
    assert EDGE_TYPE_MANIFEST_DEPENDENCY in DEPENDENCY_EDGE_TYPES


def test_architecture_layer_filters_shared_containment_by_endpoint_type():
    graph = CodeGraph("repo")
    graph.add_file_node("src/app.py")
    graph.add_symbol_node("src/app.py:main", 0, 0, 1, NODE_TYPE_FUNCTION)
    graph.add_architecture_vertex("workspace://root", {"type": NODE_TYPE_WORKSPACE})
    graph.add_architecture_vertex(
        "project://.", {"type": NODE_TYPE_PROJECT, "project_id": "project://."}
    )
    graph.add_architecture_edge("workspace://root", "project://.", EDGE_TYPE_CONTAIN)
    graph.add_architecture_edge("project://.", "src/app.py", EDGE_TYPE_CONTAIN)
    graph._add_edge("src/app.py", "src/app.py:main", EDGE_TYPE_CONTAIN)
    graph.add_architecture_edge(
        "project://.", "project://.", EDGE_TYPE_MANIFEST_DEPENDENCY
    )

    layer = build_graph_layers(graph, use_core=False).get("architecture")
    assert len(layer.edge_ids) == 3
    assert all(
        ref.source_name.startswith(("workspace://", "project://"))
        for ref in layer.iter_edges()
    )

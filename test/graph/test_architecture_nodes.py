# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from codenib.graph.code_graph import CodeGraph
from codenib.types import (DEPENDENCY_EDGE_TYPES, EDGE_TYPE_CONTAIN,
                           EDGE_TYPE_MANIFEST_DEPENDENCY,
                           GRAPH_LAYER_ARCHITECTURE, NODE_TYPE_DIRECTORY,
                           NODE_TYPE_FILE, NODE_TYPE_FUNCTION,
                           NODE_TYPE_PROJECT, NODE_TYPE_WORKSPACE,
                           edge_types_for_graph_layer, is_architecture_node,
                           is_source_node)


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


def test_file_vertex_id_populated_by_merge_from():
    base = CodeGraph("repo")
    base.add_file_node("a.py")
    other = CodeGraph("repo")
    other.add_file_node("b.py")
    base.merge_from(other)
    assert base.file_vertex_id("a.py") is not None
    assert base.file_vertex_id("b.py") is not None


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

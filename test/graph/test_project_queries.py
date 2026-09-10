# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from codenib.graph.code_graph import CodeGraph
from codenib.graph.project_queries import resolve_project_scope
from codenib.types import NODE_TYPE_PROJECT, NODE_TYPE_WORKSPACE


def _graph() -> CodeGraph:
    graph = CodeGraph("repo")
    graph.add_architecture_vertex(
        "workspace://root",
        {"type": NODE_TYPE_WORKSPACE, "workspace_id": "workspace://root"},
    )
    for project_id, path, display in (
        ("project://apps/web", "apps/web", "web"),
        ("project://libs/web", "libs/web", "web"),
        ("project://.", ".", "root"),
    ):
        graph.add_architecture_vertex(
            project_id,
            {
                "type": NODE_TYPE_PROJECT,
                "project_id": project_id,
                "project_path": path,
                "display_name": display,
                "ownership_complete": True,
                "sharing_complete": True,
            },
        )
    graph.add_file_node("apps/web/src/app.py")
    graph.graph.vs[graph.name_to_vertex["apps/web/src/app.py"]][
        "project_id"
    ] = "project://apps/web"
    return graph


def test_scope_prefers_explicit_project_id():
    result = resolve_project_scope(
        _graph(), project_id="project://apps/web", file_path="libs/web/a.py"
    )
    assert result.status == "explicit"
    assert result.single_project_id == "project://apps/web"


def test_scope_uses_longest_project_path_for_file():
    result = resolve_project_scope(_graph(), file_path="apps/web/src/app.py")
    assert result.status == "file_owner"
    assert result.single_project_id == "project://apps/web"


def test_scope_does_not_guess_ambiguous_display_name():
    result = resolve_project_scope(_graph(), query="web")
    assert result.status == "ambiguous"
    assert result.single_project_id is None
    assert {item["project_id"] for item in result.candidates} == {
        "project://apps/web",
        "project://libs/web",
    }


def test_scope_reports_invalid_file_path():
    result = resolve_project_scope(_graph(), file_path="../outside.py")
    assert result.status == "unresolved"
    assert result.diagnostics[0]["kind"] == "invalid_file_path"

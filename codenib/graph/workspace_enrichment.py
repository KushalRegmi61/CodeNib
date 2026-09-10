# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Materialize workspace discovery into the persisted CodeGraph overlay."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from ..repository_source_selection import RepositorySourceSelection
from ..types import (
    EDGE_TYPE_CONTAIN,
    EDGE_TYPE_MANIFEST_DEPENDENCY,
    NODE_TYPE_FILE,
    NODE_TYPE_PROJECT,
    NODE_TYPE_WORKSPACE,
    SOURCE_DEPENDENCY_EDGE_TYPES,
    is_architecture_node,
    is_symbol_node,
)
from ..workspace.models import (
    DependencyRecord,
    ManifestRecord,
    ProjectRecord,
    WorkspaceModel,
    WorkspaceScanBudget,
    canonical_relative_path,
    digest_json,
)
from ..workspace.resolver import ProjectPathTrie
from ..workspace.scanner import scan_workspace
from .code_graph import CodeGraph

WORKSPACE_ENRICHMENT_VERSION = 2
MAX_PROJECT_SUMMARIES = 100
MAX_DIAGNOSTICS = 100


@dataclass(frozen=True)
class WorkspaceEnrichmentResult:
    model: WorkspaceModel
    diagnostics: tuple[dict[str, object], ...]
    workspace_summary: dict[str, object]
    topology_digest: str
    metadata_digest: str
    architecture_digest: str
    project_count: int
    architecture_edge_count: int
    owned_file_count: int
    owned_symbol_count: int
    unowned_source_count: int


def canonical_graph_file_path(graph_vertex: Mapping[str, object]) -> str | None:
    """Return a canonical source path or ``None`` when ownership is unknown."""

    node_type = graph_vertex.get("type")
    value = (
        graph_vertex.get("name")
        if node_type == NODE_TYPE_FILE
        else graph_vertex.get("file")
    )
    if not isinstance(value, str) or not value:
        return None
    try:
        return canonical_relative_path(value)
    except ValueError:
        return None


def _architecture_vertices(graph: CodeGraph):
    return list(graph.iter_architecture_vertices())


def _architecture_edges(graph: CodeGraph):
    vertices = graph.graph.vs
    for edge in graph.graph.es:
        source = vertices[edge.source]
        target = vertices[edge.target]
        if is_architecture_node(
            source.attributes().get("type")
        ) or is_architecture_node(target.attributes().get("type")):
            yield edge, source, target


def topology_digest_over_graph(graph: CodeGraph) -> str:
    """Hash project topology without source-selection-dependent file edges."""

    projects = []
    for vertex in _architecture_vertices(graph):
        attrs = vertex.attributes()
        if attrs.get("type") != NODE_TYPE_PROJECT:
            continue
        projects.append(
            {
                "project_id": attrs.get("project_id", attrs.get("name")),
                "project_path": attrs.get("project_path"),
                "project_kind": attrs.get("project_kind"),
                "synthetic": attrs.get("synthetic", False),
            }
        )

    project_ids = {item["project_id"] for item in projects}
    project_edges = []
    workspace_id = None
    for vertex in _architecture_vertices(graph):
        if vertex.attributes().get("type") == NODE_TYPE_WORKSPACE:
            workspace_id = vertex.attributes().get("workspace_id", vertex["name"])
            break
    for edge, source, target in _architecture_edges(graph):
        edge_type = edge.attributes().get("type")
        source_attrs = source.attributes()
        target_attrs = target.attributes()
        if edge_type != EDGE_TYPE_MANIFEST_DEPENDENCY:
            continue
        source_id = source_attrs.get("project_id", source_attrs.get("name"))
        target_id = target_attrs.get("project_id", target_attrs.get("name"))
        if source_id in project_ids and target_id in project_ids:
            project_edges.append(
                {"source": source_id, "target": target_id, "type": edge_type}
            )

    payload = {
        "workspace_id": workspace_id,
        "projects": sorted(projects, key=lambda item: item["project_id"]),
        "project_edges": sorted(
            project_edges,
            key=lambda item: (item["source"], item["target"], item["type"]),
        ),
    }
    return digest_json(payload)


def architecture_digest(graph: CodeGraph) -> str:
    """Hash the complete persisted workspace/project architecture overlay."""

    vertices = []
    for vertex in _architecture_vertices(graph):
        attrs = vertex.attributes()
        vertices.append(
            {
                key: attrs.get(key)
                for key in (
                    "name",
                    "type",
                    "workspace_id",
                    "workspace_path",
                    "project_id",
                    "project_path",
                    "display_name",
                    "project_kind",
                    "synthetic",
                    "is_shared",
                    "ownership_complete",
                    "sharing_complete",
                    "file_count",
                    "symbol_count",
                    "consumer_count",
                )
                if key in attrs
            }
        )

    edges = []
    for edge, source, target in _architecture_edges(graph):
        edges.append(
            {
                "source": source["name"],
                "target": target["name"],
                "type": edge.attributes().get("type"),
                "manifest_evidence": edge.attributes().get("manifest_evidence") or (),
            }
        )
    return digest_json(
        {
            "vertices": sorted(vertices, key=lambda item: item["name"]),
            "edges": sorted(
                edges,
                key=lambda item: (item["source"], item["target"], item["type"]),
            ),
        }
    )


def _dependency_sort_key(dependency):
    return (
        dependency.declared_name,
        dependency.scope,
        dependency.source_manifest,
        dependency.target_project_id or "",
        dependency.resolution,
    )


def _validate_workspace_model(model: WorkspaceModel) -> None:
    project_ids = [project.project_id for project in model.projects]
    if len(project_ids) != len(set(project_ids)):
        raise ValueError("workspace model contains duplicate project ids")
    if not any(project.project_path == "." for project in model.projects):
        raise ValueError(
            "workspace model must contain a synthetic or real root project"
        )
    project_id_set = set(project_ids)
    for project in model.projects:
        canonical_relative_path(project.project_path)
        if project.project_id != f"project://{project.project_path}":
            raise ValueError(f"invalid project id for {project.project_path!r}")
        for dependency in project.dependencies:
            if (
                dependency.target_project_id is not None
                and dependency.target_project_id not in project_id_set
            ):
                raise ValueError(
                    "dependency target is not a discovered project: "
                    f"{dependency.target_project_id!r}"
                )


def workspace_model_from_graph(
    graph: CodeGraph,
    previous_summary: Mapping[str, object],
) -> WorkspaceModel:
    """Reconstruct the persisted topology for source-only incremental updates."""

    project_vertices = [
        vertex
        for vertex in graph.iter_architecture_vertices()
        if vertex.attributes().get("type") == NODE_TYPE_PROJECT
    ]
    if not project_vertices:
        raise ValueError("persisted graph has no project overlay")

    project_by_name = {vertex["name"]: vertex for vertex in project_vertices}
    dependencies: dict[str, list[DependencyRecord]] = {
        name: [] for name in project_by_name
    }
    for edge, source, target in _architecture_edges(graph):
        if edge.attributes().get("type") != EDGE_TYPE_MANIFEST_DEPENDENCY:
            continue
        source_name = source["name"]
        target_name = target["name"]
        if source_name not in project_by_name or target_name not in project_by_name:
            continue
        dependencies[source_name].append(
            DependencyRecord(
                declared_name=target_name,
                scope="persisted",
                specifier=None,
                source_manifest="<persisted>",
                resolution="persisted",
                target_project_id=target_name,
            )
        )

    projects = []
    for name in sorted(project_by_name):
        attrs = project_by_name[name].attributes()
        projects.append(
            ProjectRecord(
                project_id=name,
                project_path=str(attrs.get("project_path", ".")),
                display_name=attrs.get("display_name"),
                dependencies=dependencies[name],
                synthetic=bool(attrs.get("synthetic", False)),
                is_shared=bool(attrs.get("is_shared", False)),
                sharing_complete=bool(attrs.get("sharing_complete", False)),
                project_kind=str(attrs.get("project_kind", "persisted")),
                ownership_complete=bool(attrs.get("ownership_complete", False)),
            )
        )

    workspace = next(
        (
            vertex
            for vertex in graph.iter_architecture_vertices()
            if vertex.attributes().get("type") == NODE_TYPE_WORKSPACE
        ),
        None,
    )
    if workspace is None:
        raise ValueError("persisted graph has no workspace overlay")

    return WorkspaceModel(
        schema_version=int(previous_summary.get("schema_version", 1)),
        workspace_id=str(workspace.attributes().get("workspace_id", workspace["name"])),
        detected_systems=tuple(previous_summary.get("detected_systems", ())),
        manifests=[
            ManifestRecord(
                path=f"<persisted:{index}>",
                kind="persisted",
                adapter_id="persisted",
                adapter_version="1",
            )
            for index in range(int(previous_summary.get("manifest_count", 0)))
        ],
        projects=projects,
        diagnostics=[
            value
            for value in previous_summary.get("diagnostics", [])
            if isinstance(value, str)
        ],
        complete=bool(previous_summary.get("complete", False)),
        topology_digest=str(previous_summary.get("topology_digest", "")),
        metadata_digest=str(previous_summary.get("metadata_digest", "")),
    )


def _mark_unowned(vertex) -> None:
    # Ownership is represented by the absence of a project_id.  Keep this
    # helper explicit so callers do not accidentally assign a guessed root.
    try:
        del vertex["project_id"]
    except KeyError:
        pass


def compute_project_sharing(
    graph: CodeGraph,
    model: WorkspaceModel,
) -> dict[str, set[str]]:
    consumers = {project.project_id: set() for project in model.projects}
    vertices = graph.graph.vs
    for edge in graph.graph.es:
        edge_type = edge.attributes().get("type")
        if (
            edge_type == EDGE_TYPE_MANIFEST_DEPENDENCY
            or edge_type in SOURCE_DEPENDENCY_EDGE_TYPES
        ):
            source_attrs = vertices[edge.source].attributes()
            target_attrs = vertices[edge.target].attributes()
            source_id = source_attrs.get("project_id")
            target_id = target_attrs.get("project_id")
            if (
                isinstance(source_id, str)
                and isinstance(target_id, str)
                and source_id != target_id
                and target_id in consumers
            ):
                consumers[target_id].add(source_id)
    return consumers


def _apply_project_sharing(graph: CodeGraph, model: WorkspaceModel) -> None:
    consumers = compute_project_sharing(graph, model)
    for vertex in graph.iter_architecture_vertices():
        attrs = vertex.attributes()
        if attrs.get("type") != NODE_TYPE_PROJECT:
            continue
        project_id = attrs.get("project_id", attrs.get("name"))
        users = consumers.get(project_id, set())
        vertex["is_shared"] = bool(users)
        vertex["sharing_complete"] = bool(model.complete)
        vertex["consumer_count"] = len(users)


def prune_orphaned_architecture(
    graph: CodeGraph,
    model: WorkspaceModel | None = None,
) -> None:
    """Remove architecture edges invalidated by source-selection pruning."""

    remove_ids = []
    for edge, source, target in _architecture_edges(graph):
        edge_type = edge.attributes().get("type")
        source_type = source.attributes().get("type")
        target_type = target.attributes().get("type")
        valid = True
        if edge_type == EDGE_TYPE_CONTAIN:
            valid = (
                source_type == NODE_TYPE_WORKSPACE and target_type == NODE_TYPE_PROJECT
            ) or (source_type == NODE_TYPE_PROJECT and target_type == NODE_TYPE_FILE)
            if valid and source_type == NODE_TYPE_PROJECT:
                valid = target.attributes().get(
                    "project_id"
                ) == source.attributes().get("project_id", source["name"])
        elif edge_type == EDGE_TYPE_MANIFEST_DEPENDENCY:
            valid = (
                source_type == NODE_TYPE_PROJECT and target_type == NODE_TYPE_PROJECT
            )
        else:
            valid = False
        if not valid:
            remove_ids.append(edge.index)

    if remove_ids:
        graph.graph.delete_edges(sorted(set(remove_ids)))
        graph.invalidate_caches()
        graph._rebuild_edge_index()
        graph.build_range_indexes()
    if model is not None:
        _apply_project_sharing(graph, model)
    _set_project_counts(graph)


def _summary(
    graph: CodeGraph,
    model: WorkspaceModel,
    diagnostics: tuple[dict[str, object], ...],
    topology_digest: str,
    architecture_digest_value: str,
    owned_file_count: int,
    owned_symbol_count: int,
    unowned_source_count: int,
) -> dict[str, object]:
    (
        _,
        _,
        _,
        synthetic_root_file_count,
        synthetic_root_symbol_count,
    ) = _source_ownership_counts(graph)
    project_vertices = [
        vertex.attributes()
        for vertex in graph.iter_architecture_vertices()
        if vertex.attributes().get("type") == NODE_TYPE_PROJECT
    ]
    all_diagnostics = list(model.diagnostics) + list(diagnostics)
    from ..scip_interface.query_surface import (
        PROJECT_QUERY_SURFACE_SCHEMA_VERSION,
        QUERY_SURFACE_SCHEMA_VERSION,
        project_query_surface_sha256,
        source_query_surface_sha256,
    )

    project_summaries = [
        {
            "project_id": attrs.get("project_id", attrs.get("name")),
            "project_path": attrs.get("project_path"),
            "project_kind": attrs.get("project_kind"),
            "is_shared": attrs.get("is_shared", False),
            "file_count": attrs.get("file_count", 0),
            "symbol_count": attrs.get("symbol_count", 0),
            "consumer_count": attrs.get("consumer_count", 0),
        }
        for attrs in sorted(project_vertices, key=lambda item: item.get("name", ""))
    ]
    return {
        "schema_version": model.schema_version,
        "workspace_enrichment_version": WORKSPACE_ENRICHMENT_VERSION,
        "workspace_id": model.workspace_id,
        "detected_systems": list(model.detected_systems),
        "project_count": len(project_vertices),
        "manifest_count": len(model.manifests),
        "complete": model.complete,
        "sharing_complete": model.complete,
        "topology_digest": topology_digest,
        "metadata_digest": model.metadata_digest,
        "architecture_digest": architecture_digest_value,
        "source_query_surface_schema_version": QUERY_SURFACE_SCHEMA_VERSION,
        "source_query_surface_sha256": source_query_surface_sha256(graph),
        "query_surface_schema_version": PROJECT_QUERY_SURFACE_SCHEMA_VERSION,
        "query_surface_sha256": project_query_surface_sha256(graph),
        "owned_file_count": owned_file_count,
        "owned_symbol_count": owned_symbol_count,
        "unowned_source_count": unowned_source_count,
        "synthetic_root_file_count": synthetic_root_file_count,
        "synthetic_root_symbol_count": synthetic_root_symbol_count,
        "architecture_node_count": len(_architecture_vertices(graph)),
        "architecture_edge_count": sum(1 for _ in _architecture_edges(graph)),
        "projects": project_summaries[:MAX_PROJECT_SUMMARIES],
        "projects_truncated": len(project_summaries) > MAX_PROJECT_SUMMARIES,
        "diagnostics": all_diagnostics[:MAX_DIAGNOSTICS],
        "diagnostics_truncated": len(all_diagnostics) > MAX_DIAGNOSTICS,
    }


def build_workspace_summary(graph: CodeGraph, result: WorkspaceEnrichmentResult):
    return _summary(
        graph,
        result.model,
        result.diagnostics,
        result.topology_digest,
        result.architecture_digest,
        result.owned_file_count,
        result.owned_symbol_count,
        result.unowned_source_count,
    )


def finalize_workspace_result(
    graph: CodeGraph,
    result: WorkspaceEnrichmentResult,
) -> WorkspaceEnrichmentResult:
    """Recompute graph-dependent counts after source-selection pruning."""

    (
        owned_file_count,
        owned_symbol_count,
        unowned_source_count,
        _synthetic_root_file_count,
        _synthetic_root_symbol_count,
    ) = _source_ownership_counts(graph)
    _apply_project_sharing(graph, result.model)
    _set_project_counts(graph)
    updated = replace(
        result,
        architecture_digest=architecture_digest(graph),
        architecture_edge_count=sum(1 for _ in _architecture_edges(graph)),
        owned_file_count=owned_file_count,
        owned_symbol_count=owned_symbol_count,
        unowned_source_count=unowned_source_count,
    )
    return replace(updated, workspace_summary=build_workspace_summary(graph, updated))


def validate_workspace_graph(
    graph: CodeGraph,
    expected_summary: Mapping[str, object] | None = None,
) -> None:
    workspaces = [
        vertex
        for vertex in graph.iter_architecture_vertices()
        if vertex.attributes().get("type") == NODE_TYPE_WORKSPACE
    ]
    projects = [
        vertex
        for vertex in graph.iter_architecture_vertices()
        if vertex.attributes().get("type") == NODE_TYPE_PROJECT
    ]
    if len(workspaces) != 1:
        raise ValueError(
            f"workspace graph requires exactly one workspace, got {len(workspaces)}"
        )
    project_names = {vertex["name"] for vertex in projects}
    if len(project_names) != len(projects):
        raise ValueError("workspace graph contains duplicate project vertices")
    workspace_name = workspaces[0]["name"]
    workspace_project_edges = {
        (source["name"], target["name"])
        for edge, source, target in _architecture_edges(graph)
        if edge.attributes().get("type") == EDGE_TYPE_CONTAIN
        and source.attributes().get("type") == NODE_TYPE_WORKSPACE
        and target.attributes().get("type") == NODE_TYPE_PROJECT
    }
    missing_projects = {
        project_name
        for project_name in project_names
        if (workspace_name, project_name) not in workspace_project_edges
    }
    if missing_projects:
        raise ValueError(
            "workspace graph is missing workspace containment for: "
            + ", ".join(sorted(missing_projects))
        )

    for edge, source, target in _architecture_edges(graph):
        edge_type = edge.attributes().get("type")
        source_type = source.attributes().get("type")
        target_type = target.attributes().get("type")
        if edge_type == EDGE_TYPE_CONTAIN:
            valid = (
                source_type == NODE_TYPE_WORKSPACE and target_type == NODE_TYPE_PROJECT
            ) or (source_type == NODE_TYPE_PROJECT and target_type == NODE_TYPE_FILE)
            if source_type == NODE_TYPE_PROJECT and target_type == NODE_TYPE_FILE:
                valid = target.attributes().get(
                    "project_id"
                ) == source.attributes().get("project_id", source["name"])
        elif edge_type == EDGE_TYPE_MANIFEST_DEPENDENCY:
            valid = (
                source_type == NODE_TYPE_PROJECT and target_type == NODE_TYPE_PROJECT
            )
        else:
            valid = False
        if (
            not valid
            or edge.attributes().get("anchor_file") is not None
            or edge.attributes().get("anchor_line") is not None
        ):
            raise ValueError("invalid workspace architecture edge")

    for vertex in graph.iter_source_vertices():
        attrs = vertex.attributes()
        project_id = attrs.get("project_id")
        if project_id is not None and project_id not in project_names:
            raise ValueError(f"source vertex references unknown project {project_id!r}")

    if expected_summary is not None:
        expected_topology = expected_summary.get("topology_digest")
        if (
            expected_topology is not None
            and expected_topology != topology_digest_over_graph(graph)
        ):
            raise ValueError(
                "workspace topology digest does not match manifest metadata"
            )
        expected_architecture = expected_summary.get("architecture_digest")
        if (
            expected_architecture is not None
            and expected_architecture != architecture_digest(graph)
        ):
            raise ValueError(
                "workspace architecture digest does not match manifest metadata"
            )


def _set_project_counts(graph: CodeGraph) -> None:
    counts = {}
    vertices = graph.graph.vs
    for vertex in vertices:
        attrs = vertex.attributes()
        project_id = attrs.get("project_id")
        if not isinstance(project_id, str):
            continue
        values = counts.setdefault(project_id, {"file_count": 0, "symbol_count": 0})
        if attrs.get("type") == NODE_TYPE_FILE:
            values["file_count"] += 1
        elif is_symbol_node(attrs.get("type")):
            values["symbol_count"] += 1
    for vertex in graph.iter_architecture_vertices():
        attrs = vertex.attributes()
        if attrs.get("type") != NODE_TYPE_PROJECT:
            continue
        values = counts.get(attrs.get("project_id", attrs.get("name")), {})
        vertex["file_count"] = values.get("file_count", 0)
        vertex["symbol_count"] = values.get("symbol_count", 0)


def _source_ownership_counts(graph: CodeGraph) -> tuple[int, int, int, int, int]:
    """Return owned files/symbols, unowned sources, and root fallback counts."""

    synthetic_projects = {
        vertex["name"]
        for vertex in graph.iter_architecture_vertices()
        if vertex.attributes().get("type") == NODE_TYPE_PROJECT
        and vertex.attributes().get("synthetic") is True
    }
    owned_file_count = 0
    owned_symbol_count = 0
    unowned_source_count = 0
    synthetic_root_file_count = 0
    synthetic_root_symbol_count = 0
    for vertex in graph.iter_source_vertices():
        attrs = vertex.attributes()
        node_type = attrs.get("type")
        if node_type != NODE_TYPE_FILE and not is_symbol_node(node_type):
            continue
        project_id = attrs.get("project_id")
        if not isinstance(project_id, str) or project_id in synthetic_projects:
            unowned_source_count += 1
            if project_id in synthetic_projects:
                if node_type == NODE_TYPE_FILE:
                    synthetic_root_file_count += 1
                else:
                    synthetic_root_symbol_count += 1
        elif node_type == NODE_TYPE_FILE:
            owned_file_count += 1
        else:
            owned_symbol_count += 1
    return (
        owned_file_count,
        owned_symbol_count,
        unowned_source_count,
        synthetic_root_file_count,
        synthetic_root_symbol_count,
    )


def enrich_graph_with_workspace(
    graph: CodeGraph,
    repo_root: str,
    *,
    source_selection: RepositorySourceSelection,
    budget: WorkspaceScanBudget | None = None,
    workspace_model: WorkspaceModel | None = None,
) -> WorkspaceEnrichmentResult:
    model = workspace_model or scan_workspace(
        repo_root,
        source_selection=source_selection,
        budget=budget,
    )
    _validate_workspace_model(model)
    graph.remove_architecture_overlay()

    graph.add_architecture_vertex(
        model.workspace_id,
        {
            "type": NODE_TYPE_WORKSPACE,
            "workspace_id": model.workspace_id,
            "workspace_path": ".",
            "synthetic": False,
        },
    )
    for project in sorted(model.projects, key=lambda item: item.project_id):
        graph.add_architecture_vertex(
            project.project_id,
            {
                "type": NODE_TYPE_PROJECT,
                "project_id": project.project_id,
                "project_path": project.project_path,
                "display_name": project.display_name,
                "project_kind": project.project_kind,
                "synthetic": project.synthetic,
                "ownership_complete": project.ownership_complete,
                "sharing_complete": project.sharing_complete,
                "is_shared": False,
            },
        )
        graph.add_architecture_edge(
            model.workspace_id, project.project_id, EDGE_TYPE_CONTAIN
        )

    owner_trie = ProjectPathTrie(model.projects)
    unowned_source_count = 0
    owned_file_count = 0
    owned_symbol_count = 0
    with graph.batch_edges():
        for vertex in graph.iter_source_vertices():
            attrs = vertex.attributes()
            node_type = attrs.get("type")
            if node_type != NODE_TYPE_FILE and not is_symbol_node(node_type):
                continue
            relative_path = canonical_graph_file_path(attrs)
            if relative_path is None:
                _mark_unowned(vertex)
                unowned_source_count += 1
                continue
            project = owner_trie.lookup(relative_path)
            if project is None:
                raise AssertionError(
                    "ProjectPathTrie.lookup unexpectedly returned None"
                )
            vertex["project_id"] = project.project_id
            if project.synthetic:
                unowned_source_count += 1
            elif node_type == NODE_TYPE_FILE:
                owned_file_count += 1
            elif is_symbol_node(node_type):
                owned_symbol_count += 1
            if node_type == NODE_TYPE_FILE:
                graph.add_architecture_edge(
                    project.project_id,
                    vertex["name"],
                    EDGE_TYPE_CONTAIN,
                )

    local_diagnostics = []
    for project in sorted(model.projects, key=lambda item: item.project_id):
        for dependency in sorted(project.dependencies, key=_dependency_sort_key):
            target_id = dependency.target_project_id
            if target_id is None:
                continue
            if target_id == project.project_id:
                local_diagnostics.append(
                    {
                        "kind": "self_dependency",
                        "project_id": project.project_id,
                        "declared_name": dependency.declared_name,
                        "source_manifest": dependency.source_manifest,
                    }
                )
                continue
            graph.add_architecture_edge(
                project.project_id,
                target_id,
                EDGE_TYPE_MANIFEST_DEPENDENCY,
                manifest_evidence=(
                    {
                        "declared_name": dependency.declared_name,
                        "scope": dependency.scope,
                        "specifier": dependency.specifier,
                        "source_manifest": dependency.source_manifest,
                        "resolution": dependency.resolution,
                    },
                ),
            )

    _apply_project_sharing(graph, model)
    _set_project_counts(graph)
    topology_digest = topology_digest_over_graph(graph)
    if topology_digest != model.topology_digest:
        raise ValueError(
            "workspace topology digest mismatch between scanner and graph overlay"
        )
    result_without_summary = WorkspaceEnrichmentResult(
        model=model,
        diagnostics=tuple(local_diagnostics),
        workspace_summary={},
        topology_digest=topology_digest,
        metadata_digest=model.metadata_digest,
        architecture_digest=architecture_digest(graph),
        project_count=len(model.projects),
        architecture_edge_count=sum(1 for _ in _architecture_edges(graph)),
        owned_file_count=owned_file_count,
        owned_symbol_count=owned_symbol_count,
        unowned_source_count=unowned_source_count,
    )
    result = WorkspaceEnrichmentResult(
        **{
            **result_without_summary.__dict__,
            "workspace_summary": build_workspace_summary(graph, result_without_summary),
        }
    )
    return result


__all__ = [
    "WorkspaceEnrichmentResult",
    "architecture_digest",
    "build_workspace_summary",
    "canonical_graph_file_path",
    "compute_project_sharing",
    "enrich_graph_with_workspace",
    "finalize_workspace_result",
    "prune_orphaned_architecture",
    "topology_digest_over_graph",
    "validate_workspace_graph",
    "workspace_model_from_graph",
]

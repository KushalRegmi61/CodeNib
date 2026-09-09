# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Project-level queries over the persisted workspace graph overlay.

The source dependency analyzer remains intentionally source-only.  This
module performs a bounded, deterministic roll-up after source traversal so
project edges are derived from existing graph facts instead of materialized as
another dependency database.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..types import (
    EDGE_TYPE_MANIFEST_DEPENDENCY,
    SOURCE_DEPENDENCY_EDGE_TYPES,
    is_architecture_node,
    is_source_node,
)
from .code_graph import CodeGraph


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    source_project_id: str
    target_project_id: str
    edge_type: str
    source_node: str | None
    target_node: str | None


@dataclass(frozen=True, slots=True)
class ProjectQueryResult:
    nodes: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    diagnostics: tuple[dict[str, object], ...]
    complete: bool
    backend: str


def _vertex(graph: CodeGraph, name: str) -> Mapping[str, Any] | None:
    index = graph.name_to_vertex.get(name)
    if index is None:
        return None
    return graph.graph.vs[index].attributes()


def _project_id(attributes: Mapping[str, Any]) -> str | None:
    value = attributes.get("project_id")
    return value if isinstance(value, str) and value else None


def _project_vertex(graph: CodeGraph, project_id: str) -> Mapping[str, Any] | None:
    attributes = _vertex(graph, project_id)
    if attributes is None or attributes.get("type") != "project":
        return None
    return attributes


def _project_payload(
    graph: CodeGraph,
    project_id: str,
    *,
    depth: int,
) -> dict[str, Any]:
    attributes = _project_vertex(graph, project_id) or {}
    return {
        "project_id": project_id,
        "project_path": attributes.get("project_path"),
        "display_name": attributes.get("display_name"),
        "project_kind": attributes.get("project_kind"),
        "is_shared": attributes.get("is_shared", False),
        "ownership_complete": attributes.get("ownership_complete"),
        "sharing_complete": attributes.get("sharing_complete"),
        "depth": depth,
    }


def _edge_key(source: str, target: str, edge_type: str) -> tuple[str, str, str]:
    return source, target, edge_type


def _source_neighbors(
    graph: CodeGraph,
    vertex_id: int,
    *,
    direction: str,
) -> Iterable[tuple[int, int, str, str]]:
    """Yield ``(neighbor, source, target, type)`` source dependency edges."""

    for edge in graph.graph.es:
        edge_type = edge.attributes().get("type")
        if edge_type not in SOURCE_DEPENDENCY_EDGE_TYPES:
            continue
        if direction == "forward" and edge.source != vertex_id:
            continue
        if direction == "backward" and edge.target != vertex_id:
            continue
        if direction == "forward":
            neighbor = edge.target
        else:
            neighbor = edge.source
        source_attrs = graph.graph.vs[edge.source].attributes()
        target_attrs = graph.graph.vs[edge.target].attributes()
        if not is_source_node(source_attrs.get("type")) or not is_source_node(
            target_attrs.get("type")
        ):
            continue
        yield neighbor, edge.source, edge.target, str(edge_type)


def _complete_for_project(graph: CodeGraph, project_id: str) -> bool:
    attributes = _project_vertex(graph, project_id)
    if attributes is None or attributes.get("synthetic", False):
        return False
    return bool(
        attributes.get("ownership_complete", True)
        and attributes.get("sharing_complete", True)
    )


def project_dependency_subgraph(
    graph: CodeGraph,
    symbol: str,
    *,
    direction: str = "both",
    depth: int = 2,
    max_nodes: int = 60,
    max_edges: int = 400,
) -> dict[str, Any]:
    """Return a bounded project roll-up of a source dependency traversal."""

    normalized_direction = (direction or "").strip().lower()
    if normalized_direction in {"impact", "callers"}:
        directions = ("backward",)
        output_direction = "impact"
    elif normalized_direction in {"dependencies", "dependency", "callees"}:
        directions = ("forward",)
        output_direction = "dependencies"
    elif normalized_direction == "both":
        directions = ("forward", "backward")
        output_direction = "both"
    else:
        raise ValueError("direction must be 'impact', 'dependencies', or 'both'.")

    canonical, candidates = graph.resolve_symbol(symbol)
    if canonical is None:
        return {
            "granularity": "project",
            "root": symbol,
            "direction": output_direction,
            "nodes": [],
            "edges": [],
            "truncated": False,
            "complete": False,
            "diagnostics": [
                {
                    "kind": "unresolved_symbol",
                    "symbol": symbol,
                    "candidates": candidates,
                }
            ],
        }

    root_id = graph.name_to_vertex[canonical]
    max_nodes = max(1, int(max_nodes))
    max_edges = max(1, int(max_edges))
    max_source_nodes = max(max_nodes * 4, max_nodes)
    max_source_edges = max(max_edges * 4, max_edges)
    depths: dict[int, int] = {root_id: 0}
    raw_edges: list[tuple[int, int, str]] = []
    seen_source_edges: set[tuple[int, int, str]] = set()
    queue: deque[tuple[int, int]] = deque([(root_id, 0)])
    truncated = False

    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for scan_direction in directions:
            for neighbor, source, target, edge_type in _source_neighbors(
                graph, current, direction=scan_direction
            ):
                if len(raw_edges) >= max_source_edges:
                    truncated = True
                    break
                edge_key = (source, target, edge_type)
                if edge_key in seen_source_edges:
                    continue
                seen_source_edges.add(edge_key)
                raw_edges.append((source, target, edge_type))
                next_depth = current_depth + 1
                old_depth = depths.get(neighbor)
                if old_depth is None:
                    if len(depths) >= max_source_nodes:
                        truncated = True
                        continue
                    depths[neighbor] = next_depth
                    queue.append((neighbor, next_depth))
                elif next_depth < old_depth:
                    depths[neighbor] = next_depth
                    queue.append((neighbor, next_depth))
            if truncated and len(raw_edges) >= max_source_edges:
                break

    project_depths: dict[str, int] = {}
    complete = True
    source_project_by_id: dict[int, str] = {}
    for vertex_id, node_depth in depths.items():
        attributes = graph.graph.vs[vertex_id].attributes()
        project_id = _project_id(attributes)
        if project_id is None:
            complete = False
            continue
        source_project_by_id[vertex_id] = project_id
        project_depths[project_id] = min(
            node_depth,
            project_depths.get(project_id, node_depth),
        )
        complete = complete and _complete_for_project(graph, project_id)

    edge_evidence: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for source, target, edge_type in raw_edges:
        source_project = source_project_by_id.get(source)
        target_project = source_project_by_id.get(target)
        if source_project is None or target_project is None:
            complete = False
            continue
        if source_project == target_project:
            continue
        key = _edge_key(source_project, target_project, edge_type)
        edge_evidence.setdefault(key, []).append(
            {
                "source_node": graph.graph.vs[source]["name"],
                "target_node": graph.graph.vs[target]["name"],
            }
        )

    project_ids = set(project_depths)
    for edge in graph.graph.es:
        if edge.attributes().get("type") != EDGE_TYPE_MANIFEST_DEPENDENCY:
            continue
        source_name = graph.graph.vs[edge.source]["name"]
        target_name = graph.graph.vs[edge.target]["name"]
        if source_name not in project_ids or target_name not in project_ids:
            continue
        if source_name == target_name:
            continue
        key = _edge_key(source_name, target_name, EDGE_TYPE_MANIFEST_DEPENDENCY)
        edge_evidence.setdefault(key, []).append({"manifest": True})

    ordered_projects = sorted(
        project_ids,
        key=lambda project_id: (project_depths[project_id], project_id),
    )
    if len(ordered_projects) > max_nodes:
        ordered_projects = ordered_projects[:max_nodes]
        project_ids = set(ordered_projects)
        truncated = True

    nodes = [
        _project_payload(graph, project_id, depth=project_depths[project_id])
        for project_id in ordered_projects
    ]
    edges = []
    for key in sorted(edge_evidence):
        source_project, target_project, edge_type = key
        if source_project not in project_ids or target_project not in project_ids:
            continue
        evidence = edge_evidence[key]
        edges.append(
            {
                "source": source_project,
                "target": target_project,
                "type": edge_type,
                "evidence_count": len(evidence),
                "evidence": evidence[:100],
            }
        )
        if len(edges) >= max_edges:
            truncated = True
            break

    return {
        "granularity": "project",
        "root": _project_id(_vertex(graph, canonical) or {}) or canonical,
        "direction": output_direction,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "complete": complete,
        "diagnostics": [],
    }


def find_projects_using(
    graph: CodeGraph,
    symbol: str,
    *,
    max_projects: int = 100,
    max_evidence: int = 200,
) -> dict[str, Any]:
    """Find external projects with direct source or manifest evidence."""

    canonical, candidates = graph.resolve_symbol(symbol)
    if canonical is None:
        return {
            "symbol": symbol,
            "target_project_id": None,
            "projects": [],
            "evidence": [],
            "complete": False,
            "diagnostics": [
                {
                    "kind": "unresolved_symbol",
                    "symbol": symbol,
                    "candidates": candidates,
                }
            ],
        }

    target_attrs = _vertex(graph, canonical) or {}
    target_project = _project_id(target_attrs)
    if target_project is None:
        return {
            "symbol": canonical,
            "target_project_id": None,
            "projects": [],
            "evidence": [],
            "complete": False,
            "diagnostics": [{"kind": "target_has_no_project", "symbol": canonical}],
        }

    evidence: list[dict[str, Any]] = []
    consumer_ids: set[str] = set()
    target_id = graph.name_to_vertex[canonical]
    for edge in graph.graph.es:
        edge_type = edge.attributes().get("type")
        if edge_type in SOURCE_DEPENDENCY_EDGE_TYPES and edge.target == target_id:
            source_attrs = graph.graph.vs[edge.source].attributes()
            source_project = _project_id(source_attrs)
            if source_project and source_project != target_project:
                consumer_ids.add(source_project)
                evidence.append(
                    {
                        "source_project_id": source_project,
                        "target_project_id": target_project,
                        "edge_type": edge_type,
                        "source_node": graph.graph.vs[edge.source]["name"],
                        "target_node": canonical,
                    }
                )
        elif edge_type == EDGE_TYPE_MANIFEST_DEPENDENCY and edge.target == graph.name_to_vertex.get(
            target_project
        ):
            source_project = graph.graph.vs[edge.source]["name"]
            if source_project != target_project:
                consumer_ids.add(source_project)
                evidence.append(
                    {
                        "source_project_id": source_project,
                        "target_project_id": target_project,
                        "edge_type": edge_type,
                        "source_node": None,
                        "target_node": target_project,
                    }
                )

    ordered = sorted(consumer_ids)
    truncated = len(ordered) > max(1, int(max_projects))
    ordered = ordered[: max(1, int(max_projects))]
    allowed = set(ordered)
    evidence = [item for item in evidence if item["source_project_id"] in allowed]
    evidence.sort(
        key=lambda item: (
            item["source_project_id"],
            item["edge_type"],
            item["source_node"] or "",
        )
    )
    if len(evidence) > max(1, int(max_evidence)):
        evidence = evidence[: max(1, int(max_evidence))]
        truncated = True

    complete = _complete_for_project(graph, target_project)
    complete = complete and all(_complete_for_project(graph, item) for item in ordered)
    return {
        "symbol": canonical,
        "target_project_id": target_project,
        "projects": [_project_payload(graph, item, depth=1) for item in ordered],
        "evidence": evidence,
        "complete": complete,
        "truncated": truncated,
        "diagnostics": [],
    }


__all__ = [
    "ProjectEvidence",
    "ProjectQueryResult",
    "find_projects_using",
    "project_dependency_subgraph",
]

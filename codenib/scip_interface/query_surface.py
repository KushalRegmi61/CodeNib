# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Canonical digest of the ordered symbol-query graph surface.

The framing is shared with ``core/`` but this module depends only on Python's
standard library.  Callers supply a CodeGraph-compatible duck type; importing
this module never imports igraph or the persisted graph implementation.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from typing import Any

from ..types import is_architecture_node

QUERY_SURFACE_SCHEMA_VERSION = 1
PROJECT_QUERY_SURFACE_SCHEMA_VERSION = 3
_MAGIC = b"CodeNib-FactQuery-Surface\0"
_PROJECT_MAGIC = b"CodeNib-ProjectQuery-Surface\0"


def _attributes(item: object, *, label: str) -> Mapping[str, Any]:
    method = getattr(item, "attributes", None)
    value = method() if callable(method) else item
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} attributes must be a mapping")
    return value


def _required_string(value: object, *, label: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be strict UTF-8") from exc
    return b"\x01" + struct.pack(">Q", len(encoded)) + encoded


def _optional_string(value: object, *, label: str) -> bytes:
    if value is None:
        return b"\x00"
    return _required_string(value, label=label)


def _required_int(value: object, *, label: str) -> bytes:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    try:
        return b"\x01" + struct.pack(">q", value)
    except struct.error as exc:
        raise ValueError(f"{label} is outside signed 64-bit range") from exc


def _optional_int(value: object, *, label: str) -> bytes:
    if value is None:
        return b"\x00"
    return _required_int(value, label=label)


def _optional_bool(value: object, *, label: str) -> bytes:
    if value is None:
        return b"\x00"
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return b"\x02" if value else b"\x01"


def _manifest_evidence_bytes(value: object, *, label: str) -> bytes:
    """Frame normalized manifest evidence without relying on pickle ordering."""

    if value is None:
        return struct.pack(">Q", 0)
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} must be a tuple or list")
    fields = (
        "declared_name",
        "scope",
        "specifier",
        "source_manifest",
        "resolution",
    )
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        unknown = set(item) - set(fields)
        if unknown:
            raise ValueError(
                f"{label}[{index}] contains unknown fields: "
                + ", ".join(sorted(str(field) for field in unknown))
            )
        for field in fields:
            field_value = item.get(field)
            if field == "specifier":
                valid = field_value is None or (type(field_value) is str)
            else:
                valid = type(field_value) is str and bool(field_value)
            if not valid:
                raise ValueError(f"{label}[{index}].{field} is invalid")
        rows.append(tuple(item.get(field) for field in fields))
    rows = sorted(set(rows), key=lambda row: tuple(value or "" for value in row))
    encoded = bytearray(struct.pack(">Q", len(rows)))
    for row in rows:
        for value in row:
            encoded.extend(_optional_string(value, label=f"{label}.field"))
    return bytes(encoded)


def _query_surface_sha256(graph: object, *, source_only: bool = False) -> str:
    """Hash vertices and edges in their exact graph index order.

    The source-only projection is the Phase 1a native compatibility surface:
    workspace/project attributes remain Python-persisted, while native query
    proofs continue to cover only source vertices and source edges.
    """

    storage = getattr(graph, "graph", graph)
    all_vertices = list(storage.vs)
    vertex_ids = [
        index
        for index, vertex in enumerate(all_vertices)
        if not source_only or not is_architecture_node(vertex.attributes().get("type"))
    ]
    selected_ids = set(vertex_ids)
    edges = [
        edge
        for edge in storage.es
        if not source_only
        or (edge.source in selected_ids and edge.target in selected_ids)
    ]
    digest = hashlib.sha256()
    digest.update(_MAGIC)
    digest.update(struct.pack(">I", QUERY_SURFACE_SCHEMA_VERSION))
    digest.update(struct.pack(">Q", len(vertex_ids)))

    for position, vertex_id in enumerate(vertex_ids):
        vertex = all_vertices[vertex_id]
        attrs = _attributes(vertex, label=f"vertex[{position}]")
        name = attrs.get("name")
        digest.update(b"V")
        digest.update(_required_string(name, label=f"vertex[{position}].name"))
        digest.update(
            _required_string(attrs.get("type"), label=f"vertex[{position}].type")
        )
        digest.update(
            _optional_string(attrs.get("file"), label=f"vertex[{position}].file")
        )
        for field in ("start_line", "end_line", "selection_line"):
            digest.update(
                _optional_int(attrs.get(field), label=f"vertex[{position}].{field}")
            )
        for field in ("unified_name", "symbol_kind"):
            digest.update(
                _optional_string(attrs.get(field), label=f"vertex[{position}].{field}")
            )
        digest.update(
            _optional_bool(
                attrs.get("has_definition"),
                label=f"vertex[{position}].has_definition",
            )
        )

    digest.update(struct.pack(">Q", len(edges)))
    for position, edge in enumerate(edges):
        attrs = _attributes(edge, label=f"edge[{position}]")
        source = getattr(edge, "source", None)
        target = getattr(edge, "target", None)
        if (
            type(source) is not int
            or type(target) is not int
            or source < 0
            or target < 0
            or source >= len(all_vertices)
            or target >= len(all_vertices)
        ):
            raise ValueError(f"edge[{position}] has an invalid endpoint")
        digest.update(b"E")
        digest.update(_required_int(source, label=f"edge[{position}].source"))
        digest.update(_required_int(target, label=f"edge[{position}].target"))
        digest.update(
            _required_string(attrs.get("type"), label=f"edge[{position}].type")
        )
        digest.update(
            _optional_string(
                attrs.get("anchor_file"), label=f"edge[{position}].anchor_file"
            )
        )
        digest.update(
            _optional_int(
                attrs.get("anchor_line"), label=f"edge[{position}].anchor_line"
            )
        )
    return digest.hexdigest()


def query_surface_sha256(graph: object, *, source_only: bool = False) -> str:
    """Hash the selected graph surface using the canonical framing."""

    return _query_surface_sha256(graph, source_only=source_only)


def source_query_surface_sha256(graph: object) -> str:
    """Hash only the source-compatible graph projection."""

    storage = getattr(graph, "graph", graph)
    if not any(
        is_architecture_node(vertex.attributes().get("type")) for vertex in storage.vs
    ):
        # Preserve the existing injectable receipt seam for source-only graph
        # builders and tests; the projection is identical in this case.
        return query_surface_sha256(graph)
    return _query_surface_sha256(graph, source_only=True)


def _project_attributes(attrs: Mapping[str, Any], *, label: str) -> bytes:
    """Frame the Phase 1b architecture attributes in a stable order."""

    digest = bytearray()
    for field in (
        "project_id",
        "workspace_id",
        "project_path",
        "workspace_path",
        "display_name",
        "project_kind",
    ):
        digest.extend(_optional_string(attrs.get(field), label=f"{label}.{field}"))
    for field in (
        "synthetic",
        "ownership_complete",
        "sharing_complete",
        "is_shared",
    ):
        digest.extend(_optional_bool(attrs.get(field), label=f"{label}.{field}"))
    for field in ("file_count", "symbol_count", "consumer_count"):
        digest.extend(_optional_int(attrs.get(field), label=f"{label}.{field}"))
    return bytes(digest)


def _validate_project_vertex(attrs: Mapping[str, Any], *, label: str) -> None:
    node_type = attrs.get("type")
    name = attrs.get("name")
    if node_type == "workspace":
        if (
            not isinstance(name, str)
            or not name.startswith("workspace://")
            or name == "workspace://"
            or attrs.get("workspace_id") != name
            or not isinstance(attrs.get("workspace_path"), str)
        ):
            raise ValueError(f"{label} has invalid workspace identity attributes")
    elif node_type == "project":
        if (
            not isinstance(name, str)
            or not name.startswith("project://")
            or name == "project://"
            or attrs.get("project_id") != name
            or not isinstance(attrs.get("project_path"), str)
        ):
            raise ValueError(f"{label} has invalid project identity attributes")
    if attrs.get("project_id") is not None:
        value = attrs["project_id"]
        if (
            not isinstance(value, str)
            or not value.startswith("project://")
            or value == "project://"
            or "\\" in value
        ):
            raise ValueError(f"{label}.project_id is invalid")


def project_query_surface_sha256(graph: object) -> str:
    """Hash the complete workspace/project-aware graph surface.

    The v1 source surface deliberately remains unchanged.  This v3 surface is
    canonicalized by stable vertex/edge identity so a Python graph and a
    native overlay projection can independently produce the same receipt.
    """

    storage = getattr(graph, "graph", graph)
    vertices = list(storage.vs)
    vertex_rows = []
    for index, vertex in enumerate(vertices):
        attrs = _attributes(vertex, label=f"vertex[{index}]")
        name = attrs.get("name")
        node_type = attrs.get("type")
        if type(name) is not str or type(node_type) is not str:
            raise ValueError(f"vertex[{index}] requires string name and type")
        if node_type in {"workspace", "project"}:
            _validate_project_vertex(attrs, label=f"vertex[{index}]")
        vertex_rows.append((name, node_type, index, attrs))
    vertex_rows.sort(key=lambda row: (row[0], row[1], row[2]))
    canonical_position = {
        index: position for position, (_, _, index, _) in enumerate(vertex_rows)
    }

    digest = hashlib.sha256()
    digest.update(_PROJECT_MAGIC)
    digest.update(struct.pack(">I", PROJECT_QUERY_SURFACE_SCHEMA_VERSION))
    digest.update(struct.pack(">Q", len(vertex_rows)))
    for position, (name, node_type, _index, attrs) in enumerate(vertex_rows):
        digest.update(b"V")
        digest.update(_required_string(name, label=f"vertex[{position}].name"))
        digest.update(_required_string(node_type, label=f"vertex[{position}].type"))
        digest.update(
            _optional_string(attrs.get("file"), label=f"vertex[{position}].file")
        )
        digest.update(_project_attributes(attrs, label=f"vertex[{position}]"))
        for field in ("start_line", "end_line", "selection_line"):
            digest.update(
                _optional_int(attrs.get(field), label=f"vertex[{position}].{field}")
            )
        for field in ("unified_name", "symbol_kind"):
            digest.update(
                _optional_string(attrs.get(field), label=f"vertex[{position}].{field}")
            )
        digest.update(
            _optional_bool(
                attrs.get("has_definition"), label=f"vertex[{position}].has_definition"
            )
        )

    edge_rows = []
    for index, edge in enumerate(storage.es):
        attrs = _attributes(edge, label=f"edge[{index}]")
        source = getattr(edge, "source", None)
        target = getattr(edge, "target", None)
        if source not in canonical_position or target not in canonical_position:
            raise ValueError(f"edge[{index}] has an invalid endpoint")
        edge_type = attrs.get("type")
        if type(edge_type) is not str:
            raise ValueError(f"edge[{index}].type must be a string")
        source_type = vertices[source].attributes().get("type")
        target_type = vertices[target].attributes().get("type")
        architecture_edge = source_type in {"workspace", "project"} or target_type in {
            "workspace",
            "project",
        }
        if architecture_edge:
            valid = (
                edge_type == "contain"
                and (
                    (source_type == "workspace" and target_type == "project")
                    or (source_type == "project" and target_type == "file")
                )
            ) or (
                edge_type == "depends_on_manifest"
                and source_type == "project"
                and target_type == "project"
            )
            if (
                not valid
                or attrs.get("anchor_file") is not None
                or attrs.get("anchor_line") is not None
            ):
                raise ValueError(f"edge[{index}] is an invalid architecture edge")
            if (
                edge_type != "depends_on_manifest"
                and attrs.get("manifest_evidence") is not None
            ):
                raise ValueError(
                    f"edge[{index}] has manifest evidence on a non-manifest edge"
                )
        elif edge_type != "reference" and (
            attrs.get("anchor_file") is not None or attrs.get("anchor_line") is not None
        ):
            raise ValueError(f"edge[{index}] has anchors on a non-reference edge")
        elif attrs.get("manifest_evidence") is not None:
            raise ValueError(
                f"edge[{index}] has manifest evidence on a non-manifest edge"
            )
        edge_rows.append(
            (
                vertices[source]["name"],
                vertices[target]["name"],
                edge_type,
                attrs.get("anchor_file"),
                attrs.get("anchor_line"),
                attrs.get("manifest_evidence"),
                canonical_position[source],
                canonical_position[target],
            )
        )

    def optional_sort(value: object) -> tuple[int, object]:
        return (0, "") if value is None else (1, value)

    edge_rows.sort(
        key=lambda row: (
            row[0],
            row[1],
            row[2],
            optional_sort(row[3]),
            optional_sort(row[4]),
            _manifest_evidence_bytes(row[5], label="edge.sort.manifest_evidence"),
            row[6],
            row[7],
        )
    )
    digest.update(struct.pack(">Q", len(edge_rows)))
    for position, (
        source_name,
        target_name,
        edge_type,
        anchor_file,
        anchor_line,
        manifest_evidence,
        _source_position,
        _target_position,
    ) in enumerate(edge_rows):
        digest.update(b"E")
        digest.update(_required_string(source_name, label=f"edge[{position}].source"))
        digest.update(_required_string(target_name, label=f"edge[{position}].target"))
        digest.update(_required_string(edge_type, label=f"edge[{position}].type"))
        digest.update(
            _optional_string(anchor_file, label=f"edge[{position}].anchor_file")
        )
        digest.update(_optional_int(anchor_line, label=f"edge[{position}].anchor_line"))
        digest.update(
            _manifest_evidence_bytes(
                manifest_evidence,
                label=f"edge[{position}].manifest_evidence",
            )
        )
    return digest.hexdigest()


__all__ = [
    "QUERY_SURFACE_SCHEMA_VERSION",
    "PROJECT_QUERY_SURFACE_SCHEMA_VERSION",
    "query_surface_sha256",
    "project_query_surface_sha256",
    "source_query_surface_sha256",
]

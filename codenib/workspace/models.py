# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Stable data contracts for build-system workspace discovery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping


def canonical_relative_path(value: str) -> str:
    """Normalize one repository-relative path without resolving symlinks."""

    normalized = value.replace("\\", "/").strip("/")
    if normalized in {"", "."}:
        return "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"repository path escapes root: {value!r}")
    return path.as_posix()


def project_id_for_path(value: str) -> str:
    return f"project://{canonical_relative_path(value)}"


def normalize_distribution_name(value: str) -> str:
    """Apply the PEP 503 distribution-name normalization."""

    return "-".join(value.strip().lower().replace("_", "-").split("-"))


@dataclass(frozen=True)
class WorkspaceScanBudget:
    """Bound filesystem and parser work for very large repositories."""

    max_manifest_files: int = 250_000
    max_manifest_bytes_per_file: int = 8 * 1024 * 1024
    max_total_manifest_bytes: int = 1024 * 1024 * 1024
    max_project_nodes: int = 250_000
    max_dependency_records: int = 1_000_000
    max_bazel_labels_per_manifest: int = 100_000


@dataclass(frozen=True)
class ManifestRecord:
    path: str
    kind: str
    adapter_id: str
    adapter_version: str
    raw_sha256: str | None = None
    data: Mapping[str, Any] | None = None
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "raw_sha256": self.raw_sha256,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True)
class DependencyRecord:
    declared_name: str
    scope: str
    specifier: str | None
    source_manifest: str
    resolution: str
    target_project_id: str | None
    unresolved_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectRecord:
    project_id: str
    project_path: str
    display_name: str | None
    manifest_paths: list[str] = field(default_factory=list)
    manifest_kinds: list[str] = field(default_factory=list)
    dependencies: list[DependencyRecord] = field(default_factory=list)
    synthetic: bool = False
    is_shared: bool = False
    sharing_complete: bool = False
    project_kind: str = "manifest"
    ownership_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_path": self.project_path,
            "display_name": self.display_name,
            "manifest_paths": sorted(self.manifest_paths),
            "manifest_kinds": sorted(self.manifest_kinds),
            "dependencies": [
                dependency.to_dict()
                for dependency in sorted(
                    self.dependencies,
                    key=lambda item: (
                        item.declared_name,
                        item.scope,
                        item.source_manifest,
                        item.target_project_id or "",
                    ),
                )
            ],
            "synthetic": self.synthetic,
            "is_shared": self.is_shared,
            "sharing_complete": self.sharing_complete,
            "project_kind": self.project_kind,
            "ownership_complete": self.ownership_complete,
        }


@dataclass
class WorkspaceModel:
    schema_version: int
    workspace_id: str
    detected_systems: tuple[str, ...]
    manifests: list[ManifestRecord]
    projects: list[ProjectRecord]
    diagnostics: list[str]
    complete: bool
    topology_digest: str
    metadata_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "detected_systems": list(self.detected_systems),
            "manifests": [item.to_dict() for item in self.manifests],
            "projects": [item.to_dict() for item in self.projects],
            "diagnostics": list(self.diagnostics),
            "complete": self.complete,
            "topology_digest": self.topology_digest,
            "metadata_digest": self.metadata_digest,
        }

    def metadata_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "detected_systems": list(self.detected_systems),
            "project_count": len(self.projects),
            "manifest_count": len(self.manifests),
            "complete": self.complete,
            "diagnostics": list(self.diagnostics),
            "topology_digest": self.topology_digest,
            "metadata_digest": self.metadata_digest,
        }


def digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "DependencyRecord",
    "ManifestRecord",
    "ProjectRecord",
    "WorkspaceModel",
    "WorkspaceScanBudget",
    "canonical_relative_path",
    "digest_json",
    "normalize_distribution_name",
    "project_id_for_path",
]

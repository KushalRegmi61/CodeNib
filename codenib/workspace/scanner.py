# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Orchestrate one inventory across all build-system adapters."""

from __future__ import annotations

from typing import Iterable

from ..repository_source_selection import (DEFAULT_REPOSITORY_SOURCE_SELECTION,
                                           RepositorySourceSelection)
from .adapters.base import AdapterContext
from .inventory import build_manifest_inventory, raw_digest
from .models import (DependencyRecord, ProjectRecord, WorkspaceModel,
                     WorkspaceScanBudget, digest_json)
from .registry import DEFAULT_ADAPTERS, validate_registry
from .resolver import merge_projects

_SYSTEM_NAMES = {
    "bazel": "bazel",
    "cargo": "cargo",
    "node-workspace": "node",
    "turbo": "turbo",
    "python-pyproject": "python",
    "heuristic": "heuristic",
}


def _dedupe_dependencies(values: Iterable[DependencyRecord]) -> list[DependencyRecord]:
    seen: set[tuple] = set()
    output = []
    for value in values:
        key = (
            value.declared_name,
            value.scope,
            value.source_manifest,
            value.target_project_id,
            value.resolution,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return sorted(
        output,
        key=lambda item: (
            item.declared_name,
            item.scope,
            item.source_manifest,
            item.target_project_id or "",
        ),
    )


def _topology_payload(projects: list[ProjectRecord]) -> list[dict]:
    return [
        {
            "project_id": project.project_id,
            "project_path": project.project_path,
            "manifest_kinds": sorted(project.manifest_kinds),
            "project_kind": project.project_kind,
            "dependencies": [
                {
                    "target_project_id": dependency.target_project_id,
                    "scope": dependency.scope,
                    "resolution": dependency.resolution,
                }
                for dependency in project.dependencies
                if dependency.target_project_id is not None
            ],
        }
        for project in sorted(projects, key=lambda item: item.project_id)
    ]


def scan_workspace(
    repo_root: str,
    *,
    source_selection: RepositorySourceSelection = DEFAULT_REPOSITORY_SOURCE_SELECTION,
    budget: WorkspaceScanBudget | None = None,
) -> WorkspaceModel:
    budget = budget if budget is not None else WorkspaceScanBudget()
    validate_registry()
    inventory = build_manifest_inventory(
        repo_root,
        selection=source_selection,
        budget=budget,
    )
    diagnostics = list(inventory.diagnostics)
    context = AdapterContext(inventory=inventory, diagnostics=diagnostics)
    records = []
    raw_projects: list[ProjectRecord] = []

    for adapter in DEFAULT_ADAPTERS:
        discovered = list(adapter.discover(context))
        records.extend(discovered)
        context.records = records
        raw_projects.extend(adapter.create_projects(discovered, context))

    context.records = records
    projects = merge_projects(raw_projects)
    if not any(project.project_path == "." for project in projects):
        projects.insert(
            0,
            ProjectRecord(
                project_id="project://.",
                project_path=".",
                display_name=None,
                synthetic=True,
                project_kind="synthetic",
                ownership_complete=inventory.complete,
                sharing_complete=False,
            ),
        )

    if len(projects) > budget.max_project_nodes:
        diagnostics.append(
            f"project budget exceeded: max_project_nodes={budget.max_project_nodes}"
        )
        projects = projects[: budget.max_project_nodes]
        inventory_complete = False
    else:
        inventory_complete = inventory.complete

    dependency_count = 0
    for project in projects:
        dependencies: list[DependencyRecord] = []
        for adapter in DEFAULT_ADAPTERS:
            dependencies.extend(adapter.dependencies(project, projects, context))
        dependencies = _dedupe_dependencies(dependencies)
        remaining = max(0, budget.max_dependency_records - dependency_count)
        if len(dependencies) > remaining:
            diagnostics.append(
                "dependency budget exceeded: "
                f"max_dependency_records={budget.max_dependency_records}"
            )
            dependencies = dependencies[:remaining]
            inventory_complete = False
        dependency_count += len(dependencies)
        project.dependencies = dependencies

    systems = tuple(
        sorted(
            {
                _SYSTEM_NAMES[adapter.adapter_id]
                for adapter in DEFAULT_ADAPTERS
                if any(record.adapter_id == adapter.adapter_id for record in records)
            }
        )
    )
    metadata_digest = raw_digest(inventory)
    return WorkspaceModel(
        schema_version=1,
        workspace_id="workspace://root",
        detected_systems=systems,
        manifests=sorted(records, key=lambda item: item.path),
        projects=projects,
        diagnostics=sorted(set(diagnostics)),
        complete=inventory_complete and not diagnostics,
        topology_digest=digest_json(_topology_payload(projects)),
        metadata_digest=metadata_digest,
    )


__all__ = ["scan_workspace"]

# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import fnmatch
import hashlib
import json
from typing import Any, Iterable, Sequence

import yaml

from ..models import (
    DependencyRecord,
    ManifestRecord,
    ProjectRecord,
    project_id_for_path,
)
from ..resolver import unique_name_index
from .base import AdapterContext


def _parse_json(raw: bytes, path: str) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: package manifest must be an object")
    return value


def _workspace_patterns(context: AdapterContext) -> list[str]:
    patterns: list[str] = []
    root = context.inventory.raw.get("package.json")
    if root is not None:
        try:
            payload = _parse_json(root, "package.json")
            workspaces = payload.get("workspaces", [])
            if isinstance(workspaces, list):
                patterns.extend(item for item in workspaces if isinstance(item, str))
            elif isinstance(workspaces, dict):
                packages = workspaces.get("packages", [])
                if isinstance(packages, list):
                    patterns.extend(item for item in packages if isinstance(item, str))
        except ValueError as exc:
            context.diagnostics.append(str(exc))
    pnpm = context.inventory.raw.get("pnpm-workspace.yaml")
    if pnpm is not None:
        try:
            payload = yaml.safe_load(pnpm.decode("utf-8")) or {}
            packages = payload.get("packages", []) if isinstance(payload, dict) else []
            if isinstance(packages, list):
                patterns.extend(item for item in packages if isinstance(item, str))
        except (ValueError, yaml.YAMLError) as exc:
            context.diagnostics.append(f"pnpm-workspace.yaml: {exc}")
    return sorted(set(patterns))


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    normalized = path.strip("/")
    return any(
        fnmatch.fnmatch(normalized, pattern.strip("/"))
        or fnmatch.fnmatch(f"{normalized}/", pattern.strip("/").rstrip("/") + "/")
        for pattern in patterns
    )


class NodeWorkspaceAdapter:
    adapter_id = "node-workspace"
    adapter_version = "1"
    manifest_names = ("package.json", "pnpm-workspace.yaml")

    @classmethod
    def discover(cls, context: AdapterContext) -> Iterable[ManifestRecord]:
        patterns = _workspace_patterns(context)
        for path in context.inventory.paths:
            if path == "pnpm-workspace.yaml":
                try:
                    payload = (
                        yaml.safe_load(context.inventory.raw[path].decode("utf-8"))
                        or {}
                    )
                    yield ManifestRecord(
                        path=path,
                        kind="pnpm_workspace",
                        adapter_id=cls.adapter_id,
                        adapter_version=cls.adapter_version,
                        raw_sha256=f"sha256:{hashlib.sha256(context.inventory.raw[path]).hexdigest()}",
                        data=payload if isinstance(payload, dict) else {},
                    )
                except (ValueError, yaml.YAMLError) as exc:
                    context.diagnostics.append(f"{path}: {exc}")
                continue
            if not path.endswith("package.json"):
                continue
            try:
                data = _parse_json(context.inventory.raw[path], path)
            except ValueError as exc:
                context.diagnostics.append(str(exc))
                continue
            project_path = path[: -len("/package.json")] or "."
            if project_path == "." or (
                patterns and _matches_any(project_path, patterns)
            ):
                yield ManifestRecord(
                    path=path,
                    kind="node_package",
                    adapter_id=cls.adapter_id,
                    adapter_version=cls.adapter_version,
                    raw_sha256=f"sha256:{hashlib.sha256(context.inventory.raw[path]).hexdigest()}",
                    data=data,
                )

    @classmethod
    def create_projects(cls, records, context):
        for record in records:
            if record.kind != "node_package":
                continue
            path = record.path[: -len("/package.json")] or "."
            data = dict(record.data or {})
            name = data.get("name") if isinstance(data.get("name"), str) else None
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=name,
                manifest_paths=[record.path],
                manifest_kinds=[record.kind],
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        record = next(
            (item for item in context.records if item.path in project.manifest_paths),
            None,
        )
        if record is None or record.kind != "node_package":
            return []
        data = dict(record.data or {})
        # Ambiguous local names stay unresolved (never guess): duplicates are
        # dropped from the index instead of last-wins.
        by_name = unique_name_index(projects, lambda value: value)
        output = []
        for scope in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            values = data.get(scope, {})
            if not isinstance(values, dict):
                continue
            for name, specifier in sorted(values.items()):
                if not isinstance(name, str) or not isinstance(specifier, str):
                    continue
                target = by_name.get(name)
                if specifier.startswith(("workspace:", "file:", "link:")):
                    resolution = "explicit-local" if target else "unresolved"
                else:
                    resolution = "name" if target else "unresolved"
                output.append(
                    DependencyRecord(
                        declared_name=name,
                        scope=scope,
                        specifier=specifier,
                        source_manifest=record.path,
                        resolution=resolution,
                        target_project_id=target.project_id if target else None,
                        unresolved_reason=(
                            None if target else "no unique local workspace package"
                        ),
                    )
                )
        return output

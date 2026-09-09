# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 runtime
    import tomli as tomllib  # type: ignore[no-redef]

from ..models import (DependencyRecord, ManifestRecord, ProjectRecord,
                      normalize_distribution_name, project_id_for_path)
from ..resolver import unique_name_index


def _name(data: dict[str, Any]) -> str | None:
    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("name"), str):
        return project["name"]
    for section in ("poetry", "pdm"):
        tool = data.get("tool", {}).get(section, {})
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            return tool["name"]
    return None


def _requirements(data: dict[str, Any]):
    project = data.get("project", {})
    if isinstance(project, dict):
        for item in project.get("dependencies", []) or []:
            if isinstance(item, str):
                yield "runtime", item
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for scope, values in optional.items():
                for item in values or []:
                    if isinstance(item, str):
                        yield f"optional:{scope}", item
    groups = data.get("dependency-groups", {})
    if isinstance(groups, dict):
        for scope, values in groups.items():
            for item in values or []:
                if isinstance(item, str):
                    yield f"group:{scope}", item
    poetry = data.get("tool", {}).get("poetry", {})
    if isinstance(poetry, dict):
        for scope, values in (
            ("runtime", poetry.get("dependencies")),
            ("dev", poetry.get("dev-dependencies")),
        ):
            if isinstance(values, dict):
                for name, spec in values.items():
                    if name != "python":
                        yield scope, (
                            f"{name} {spec}" if isinstance(spec, str) else str(name)
                        )


def _requirement_name(value: str) -> str:
    value = value.strip()
    for marker in ("[", ";", " ", "<", ">", "=", "!", "~"):
        if marker in value:
            value = value.split(marker, 1)[0]
    return normalize_distribution_name(value)


class PythonAdapter:
    adapter_id = "python-pyproject"
    adapter_version = "1"
    manifest_names = ("pyproject.toml",)

    @classmethod
    def discover(cls, context):
        for path in context.inventory.paths:
            if not path.endswith("pyproject.toml"):
                continue
            try:
                data = tomllib.loads(context.inventory.raw[path].decode("utf-8"))
                yield ManifestRecord(
                    path=path,
                    kind="python_pyproject",
                    adapter_id=cls.adapter_id,
                    adapter_version=cls.adapter_version,
                    raw_sha256=f"sha256:{hashlib.sha256(context.inventory.raw[path]).hexdigest()}",
                    data=data,
                )
            except ValueError as exc:
                context.diagnostics.append(f"{path}: {exc}")

    @classmethod
    def create_projects(cls, records, context):
        for record in records:
            if record.kind != "python_pyproject":
                continue
            path = record.path[: -len("/pyproject.toml")] or "."
            data = dict(record.data or {})
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=_name(data),
                manifest_paths=[record.path],
                manifest_kinds=[record.kind],
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        record = next(
            (item for item in context.records if item.path in project.manifest_paths),
            None,
        )
        if record is None or record.kind != "python_pyproject":
            return []
        # Ambiguous distribution names stay unresolved (never guess).
        names = unique_name_index(projects, normalize_distribution_name)
        output = []
        for scope, raw in _requirements(dict(record.data or {})):
            name = _requirement_name(raw)
            target = names.get(name)
            output.append(
                DependencyRecord(
                    declared_name=name,
                    scope=scope,
                    specifier=raw,
                    source_manifest=record.path,
                    resolution="name" if target else "unresolved",
                    target_project_id=target.project_id if target else None,
                    unresolved_reason=(
                        None if target else "no unique local distribution"
                    ),
                )
            )
        return output

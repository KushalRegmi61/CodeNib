# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Bounded, toolchain-free discovery for Go modules and workspaces."""

from __future__ import annotations

import hashlib
import posixpath
from pathlib import PurePosixPath

from ..models import (
    DependencyRecord,
    ManifestRecord,
    ProjectRecord,
    canonical_relative_path,
    project_id_for_path,
)


def _clean_line(value: str) -> str:
    return value.split("//", 1)[0].strip()


def _parse_go_mod(text: str) -> dict:
    module = None
    requires: list[tuple[str, str | None]] = []
    replaces: dict[str, str] = {}
    block: str | None = None

    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        if block is not None:
            if line == ")":
                block = None
                continue
            values = line.split()
            if block == "require" and values:
                requires.append((values[0], values[1] if len(values) > 1 else None))
            elif block == "replace" and "=>" in values:
                left, right = line.split("=>", 1)
                replaces[left.strip().split()[0]] = right.strip().split()[0]
            continue

        values = line.split()
        if not values:
            continue
        directive = values[0]
        if directive in {"require", "replace"} and len(values) == 1:
            block = directive
            continue
        if directive == "module" and len(values) >= 2:
            module = values[1]
        elif directive == "require" and len(values) >= 2:
            requires.append((values[1], values[2] if len(values) > 2 else None))
        elif directive == "replace" and "=>" in line:
            left, right = line.split("=>", 1)
            replaces[left.strip().split()[0]] = right.strip().split()[0]

    return {
        "module": module,
        "requires": requires,
        "replaces": replaces,
    }


def _parse_go_work(text: str) -> dict:
    uses: list[str] = []
    block = False
    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        if line == "use (":
            block = True
            continue
        if block and line == ")":
            block = False
            continue
        values = line.split()
        if block and values:
            uses.append(values[0])
        elif values and values[0] == "use" and len(values) >= 2:
            uses.append(values[1])
    return {"uses": sorted(set(uses))}


class GoAdapter:
    adapter_id = "go"
    adapter_version = "1"
    manifest_names = ("go.mod", "go.work")

    @classmethod
    def discover(cls, context):
        for path in context.inventory.paths:
            if path.endswith("go.mod"):
                kind = "go_module"
                parser = _parse_go_mod
            elif path.endswith("go.work"):
                kind = "go_workspace"
                parser = _parse_go_work
            else:
                continue
            raw = context.inventory.raw[path]
            try:
                data = parser(raw.decode("utf-8"))
            except ValueError as exc:
                context.diagnostics.append(f"{path}: {exc}")
                continue
            yield ManifestRecord(
                path=path,
                kind=kind,
                adapter_id=cls.adapter_id,
                adapter_version=cls.adapter_version,
                raw_sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
                data=data,
            )

    @classmethod
    def create_projects(cls, records, context):
        workspace_records = [
            record for record in records if record.kind == "go_workspace"
        ]
        selected_paths = None
        if workspace_records:
            selected_paths = set()
            for record in workspace_records:
                workspace_path = record.path[: -len("/go.work")] or "."
                for use in (record.data or {}).get("uses", []):
                    if not isinstance(use, str):
                        continue
                    try:
                        selected_paths.add(
                            canonical_relative_path(
                                posixpath.normpath(
                                    str(PurePosixPath(workspace_path) / use)
                                )
                            )
                        )
                    except ValueError as exc:
                        context.diagnostics.append(
                            f"{record.path}: invalid use path {use!r}: {exc}"
                        )
        for record in records:
            if record.kind != "go_module":
                continue
            data = dict(record.data or {})
            module = data.get("module")
            if not isinstance(module, str) or not module:
                context.diagnostics.append(f"{record.path}: missing module directive")
                continue
            path = record.path[: -len("/go.mod")] or "."
            if selected_paths is not None and path not in selected_paths:
                continue
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=module,
                manifest_paths=[record.path],
                manifest_kinds=[record.kind],
                project_kind="go",
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        record = next(
            (item for item in context.records if item.path in project.manifest_paths),
            None,
        )
        if record is None or record.kind != "go_module":
            return []
        data = dict(record.data or {})
        by_module = {
            item.display_name: item
            for item in projects
            if isinstance(item.display_name, str)
        }
        replaces = data.get("replaces", {})
        output = []
        for module, version in data.get("requires", []):
            target = by_module.get(module)
            resolution = "module" if target else "unresolved"
            replacement = replaces.get(module)
            if replacement and replacement.startswith("."):
                target_path = PurePosixPath(
                    posixpath.normpath(
                        str(PurePosixPath(project.project_path) / replacement)
                    )
                ).as_posix()
                target = next(
                    (item for item in projects if item.project_path == target_path),
                    None,
                )
                resolution = "replace" if target else "unresolved"
            output.append(
                DependencyRecord(
                    declared_name=module,
                    scope="require",
                    specifier=version,
                    source_manifest=record.path,
                    resolution=resolution,
                    target_project_id=target.project_id if target else None,
                    unresolved_reason=(
                        None if target else "no internal Go module target"
                    ),
                )
            )
        return output


__all__ = ["GoAdapter"]

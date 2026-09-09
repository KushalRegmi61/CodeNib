# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import posixpath
from pathlib import PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from ..models import (DependencyRecord, ManifestRecord, ProjectRecord,
                      project_id_for_path)
from ..resolver import unique_name_index


def _cargo_name(data):
    package = data.get("package", {})
    return package.get("name") if isinstance(package, dict) else None


class CargoAdapter:
    adapter_id = "cargo"
    adapter_version = "1"
    manifest_names = ("Cargo.toml",)

    @classmethod
    def discover(cls, context):
        for path in context.inventory.paths:
            if not path.endswith("Cargo.toml"):
                continue
            try:
                data = tomllib.loads(context.inventory.raw[path].decode("utf-8"))
                yield ManifestRecord(
                    path=path,
                    kind="cargo_manifest",
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
            if record.kind != "cargo_manifest":
                continue
            data = dict(record.data or {})
            if not _cargo_name(data) and "workspace" not in data:
                continue
            path = record.path[: -len("/Cargo.toml")] or "."
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=_cargo_name(data),
                manifest_paths=[record.path],
                manifest_kinds=[record.kind],
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        record = next(
            (item for item in context.records if item.path in project.manifest_paths),
            None,
        )
        # Only Cargo manifests: a foreign record (e.g. package.json) can share
        # the "dependencies" key and would otherwise emit bogus edges.
        if record is None or record.kind != "cargo_manifest":
            return []
        data = dict(record.data or {})
        # Workspace-inherited names follow Cargo's hyphen/underscore
        # equivalence; ambiguous names stay unresolved (never guess).
        names = unique_name_index(projects, lambda value: value.replace("-", "_"))
        output = []
        for scope in ("dependencies", "dev-dependencies", "build-dependencies"):
            values = data.get(scope, {})
            if not isinstance(values, dict):
                continue
            for name, spec in sorted(values.items()):
                if not isinstance(name, str):
                    continue
                target = None
                resolution = "unresolved"
                specifier = spec if isinstance(spec, str) else None
                if isinstance(spec, dict):
                    path = spec.get("path")
                    if isinstance(path, str):
                        base = PurePosixPath(project.project_path)
                        target_path = PurePosixPath(
                            posixpath.normpath(str(base / path))
                        ).as_posix()
                        target = next(
                            (
                                item
                                for item in projects
                                if item.project_path == target_path
                            ),
                            None,
                        )
                        resolution = "path" if target else "unresolved"
                    elif spec.get("workspace") is True:
                        target = names.get(name.replace("-", "_"))
                        resolution = "workspace" if target else "unresolved"
                output.append(
                    DependencyRecord(
                        declared_name=name,
                        scope=scope,
                        specifier=specifier,
                        source_manifest=record.path,
                        resolution=resolution,
                        target_project_id=target.project_id if target else None,
                        unresolved_reason=(
                            None
                            if target
                            else "no internal Cargo path/workspace target"
                        ),
                    )
                )
        return output

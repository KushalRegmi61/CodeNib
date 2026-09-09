# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from ..models import (DependencyRecord, ManifestRecord, ProjectRecord,
                      project_id_for_path)

_LABEL_RE = re.compile(r"(?P<label>(?://[^\"']+|:[A-Za-z0-9_./+\-]+))")


class BazelAdapter:
    adapter_id = "bazel"
    adapter_version = "1"
    manifest_names = (
        "MODULE.bazel",
        "REPO.bazel",
        "WORKSPACE",
        "WORKSPACE.bazel",
        "BUILD",
        "BUILD.bazel",
    )

    @classmethod
    def discover(cls, context):
        for path in context.inventory.paths:
            name = PurePosixPath(path).name
            if name not in cls.manifest_names:
                continue
            kind = (
                "bazel_package"
                if name in {"BUILD", "BUILD.bazel"}
                else "bazel_workspace"
            )
            raw = context.inventory.raw[path]
            yield ManifestRecord(
                path=path,
                kind=kind,
                adapter_id=cls.adapter_id,
                adapter_version=cls.adapter_version,
                raw_sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
                data={"text": raw.decode("utf-8", errors="replace")},
            )

    @classmethod
    def create_projects(cls, records, context):
        for record in records:
            if record.kind != "bazel_package":
                continue
            path = record.path.rsplit("/", 1)[0] if "/" in record.path else "."
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=f"//{'' if path == '.' else path}",
                manifest_paths=[record.path],
                manifest_kinds=[record.kind],
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        record = next(
            (item for item in context.records if item.path in project.manifest_paths),
            None,
        )
        if record is None:
            return []
        text = str((record.data or {}).get("text", ""))
        labels = sorted(set(match.group("label") for match in _LABEL_RE.finditer(text)))
        output = []
        for label in labels:
            if label.startswith("@"):
                continue
            if label.startswith(":"):
                target_path = project.project_path
            else:
                target_path = label[2:].split(":", 1)[0] or "."
            target = next(
                (item for item in projects if item.project_path == target_path), None
            )
            output.append(
                DependencyRecord(
                    declared_name=label,
                    scope="bazel",
                    specifier=label,
                    source_manifest=record.path,
                    resolution="label" if target else "unresolved",
                    target_project_id=target.project_id if target else None,
                    unresolved_reason=(
                        None if target else "label target is not a discovered package"
                    ),
                )
            )
        if any(token in text for token in ("select(", "glob(", "depset(", "macro")):
            context.diagnostics.append(
                f"{record.path}: Bazel dependency extraction is partial for dynamic expressions"
            )
        return output

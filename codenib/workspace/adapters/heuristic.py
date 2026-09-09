# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import PurePosixPath

from ..models import ProjectRecord, project_id_for_path

_HEURISTIC_ROOTS = frozenset(
    {"apps", "packages", "libs", "services", "modules", "components", "crates", "tools"}
)


class HeuristicAdapter:
    adapter_id = "heuristic"
    adapter_version = "1"
    manifest_names = ()

    @classmethod
    def discover(cls, context):
        return []

    @classmethod
    def create_projects(cls, records, context):
        known = {item.project_path for item in records}
        candidates = set()
        for path in context.inventory.file_paths:
            parts = PurePosixPath(path).parts
            if len(parts) >= 2 and parts[0] in _HEURISTIC_ROOTS:
                candidates.add("/".join(parts[:2]))
        for path in sorted(candidates):
            if path in known:
                continue
            yield ProjectRecord(
                project_id=project_id_for_path(path),
                project_path=path,
                display_name=None,
                project_kind="heuristic",
                synthetic=False,
                ownership_complete=False,
            )

    @classmethod
    def dependencies(cls, project, projects, context):
        return []

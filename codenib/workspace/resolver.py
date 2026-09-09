# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Efficient project ownership and normalized dependency helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import ProjectRecord, canonical_relative_path


@dataclass
class _TrieNode:
    children: dict[str, "_TrieNode"]
    project: ProjectRecord | None = None

    def __init__(self) -> None:
        self.children = {}
        self.project = None


class ProjectPathTrie:
    """Longest-prefix project ownership index."""

    def __init__(self, projects: Sequence[ProjectRecord]) -> None:
        self.root = _TrieNode()
        self.synthetic_root = next(
            (project for project in projects if project.project_path == "."),
            ProjectRecord(
                project_id="project://.",
                project_path=".",
                display_name=None,
                synthetic=True,
                ownership_complete=False,
                sharing_complete=False,
            ),
        )
        ordered = sorted(
            projects, key=lambda item: (item.project_path != ".", item.project_path)
        )
        for project in ordered:
            node = self.root
            if project.project_path != ".":
                for part in project.project_path.split("/"):
                    node = node.children.setdefault(part, _TrieNode())
            node.project = project

    def lookup(self, path: str) -> ProjectRecord | None:
        node = self.root
        owner = node.project
        try:
            parts = canonical_relative_path(path).split("/")
        except ValueError:
            return owner
        for part in parts:
            node = node.children.get(part)
            if node is None:
                break
            if node.project is not None:
                owner = node.project
        return owner


def merge_projects(projects: Iterable[ProjectRecord]) -> list[ProjectRecord]:
    merged: dict[str, ProjectRecord] = {}
    for project in projects:
        existing = merged.get(project.project_path)
        if existing is None:
            merged[project.project_path] = project
            continue
        existing.manifest_paths = sorted(
            set(existing.manifest_paths + project.manifest_paths)
        )
        existing.manifest_kinds = sorted(
            set(existing.manifest_kinds + project.manifest_kinds)
        )
        if existing.display_name is None:
            existing.display_name = project.display_name
        if existing.project_kind == "heuristic" and project.project_kind != "heuristic":
            existing.project_kind = project.project_kind
            existing.ownership_complete = project.ownership_complete
        existing.synthetic = existing.synthetic and project.synthetic
    return sorted(merged.values(), key=lambda item: item.project_id)


def unique_name_index(
    projects: Sequence[ProjectRecord], normalize
) -> dict[str, ProjectRecord]:
    candidates: dict[str, list[ProjectRecord]] = {}
    for project in projects:
        if project.display_name:
            candidates.setdefault(normalize(project.display_name), []).append(project)
    return {name: values[0] for name, values in candidates.items() if len(values) == 1}


__all__ = ["ProjectPathTrie", "merge_projects", "unique_name_index"]

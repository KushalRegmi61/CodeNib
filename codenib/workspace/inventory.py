# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""One-pass manifest inventory shared by all workspace adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..repository_filters import walk_repository_files
from ..repository_source_selection import (DEFAULT_REPOSITORY_SOURCE_SELECTION,
                                           RepositorySourceSelection)
from .models import WorkspaceScanBudget, canonical_relative_path


@dataclass(frozen=True)
class ManifestInventory:
    root: Path
    paths: tuple[str, ...]
    raw: dict[str, bytes]
    file_paths: tuple[str, ...]
    diagnostics: tuple[str, ...]
    complete: bool

    def has(self, path: str) -> bool:
        return path in self.raw

    def data(self, path: str) -> bytes:
        return self.raw[path]


MANIFEST_NAMES = frozenset(
    {
        "BUILD",
        "BUILD.bazel",
        "Cargo.toml",
        "MODULE.bazel",
        "REPO.bazel",
        "WORKSPACE",
        "WORKSPACE.bazel",
        "package.json",
        "pnpm-workspace.yaml",
        "pyproject.toml",
        "turbo.json",
    }
)


def build_manifest_inventory(
    root: str | Path,
    *,
    selection: RepositorySourceSelection = DEFAULT_REPOSITORY_SOURCE_SELECTION,
    budget: WorkspaceScanBudget | None = None,
) -> ManifestInventory:
    budget = budget if budget is not None else WorkspaceScanBudget()
    root_path = Path(root).expanduser().absolute()
    raw: dict[str, bytes] = {}
    file_paths: list[str] = []
    diagnostics: list[str] = []
    total_bytes = 0
    complete = True

    for path in walk_repository_files(root_path, selection=selection):
        relative = canonical_relative_path(path.relative_to(root_path).as_posix())
        file_paths.append(relative)
        if path.name not in MANIFEST_NAMES:
            continue
        if len(raw) >= budget.max_manifest_files:
            complete = False
            diagnostics.append(
                f"manifest budget exceeded: max_manifest_files={budget.max_manifest_files}"
            )
            break
        try:
            size = path.stat().st_size
            if size > budget.max_manifest_bytes_per_file:
                complete = False
                diagnostics.append(
                    f"manifest skipped for size: path={relative!r} "
                    f"size={size} max={budget.max_manifest_bytes_per_file}"
                )
                continue
            total_bytes += size
            if total_bytes > budget.max_total_manifest_bytes:
                complete = False
                diagnostics.append(
                    "manifest budget exceeded: "
                    f"max_total_manifest_bytes={budget.max_total_manifest_bytes}"
                )
                break
            raw[relative] = path.read_bytes()
        except OSError as exc:
            complete = False
            diagnostics.append(f"manifest unreadable: path={relative!r}: {exc}")

    return ManifestInventory(
        root=root_path,
        paths=tuple(sorted(raw)),
        raw=raw,
        file_paths=tuple(sorted(file_paths)),
        diagnostics=tuple(sorted(set(diagnostics))),
        complete=complete,
    )


def raw_digest(inventory: ManifestInventory) -> str:
    digest = hashlib.sha256()
    for path in inventory.paths:
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        content = inventory.raw[path]
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


__all__ = [
    "MANIFEST_NAMES",
    "ManifestInventory",
    "build_manifest_inventory",
    "raw_digest",
]

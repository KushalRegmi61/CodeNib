# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Closed, deterministic registry for build-system adapters."""

from __future__ import annotations

from .adapters.bazel import BazelAdapter
from .adapters.cargo import CargoAdapter
from .adapters.heuristic import HeuristicAdapter
from .adapters.node import NodeWorkspaceAdapter
from .adapters.python import PythonAdapter
from .adapters.turbo import TurboAdapter

DEFAULT_ADAPTERS = (
    BazelAdapter,
    CargoAdapter,
    NodeWorkspaceAdapter,
    TurboAdapter,
    PythonAdapter,
    HeuristicAdapter,
)


def validate_registry() -> None:
    ids: set[str] = set()
    manifest_names: dict[str, str] = {}
    for adapter in DEFAULT_ADAPTERS:
        if not adapter.adapter_id or not adapter.adapter_version:
            raise ValueError(f"workspace adapter metadata is incomplete: {adapter}")
        if adapter.adapter_id in ids:
            raise ValueError(f"duplicate workspace adapter: {adapter.adapter_id}")
        ids.add(adapter.adapter_id)
        for name in adapter.manifest_names:
            owner = manifest_names.setdefault(name, adapter.adapter_id)
            if owner != adapter.adapter_id and name not in {"BUILD", "BUILD.bazel"}:
                raise ValueError(
                    f"manifest {name!r} owned by {owner} and {adapter.adapter_id}"
                )


__all__ = ["DEFAULT_ADAPTERS", "validate_registry"]

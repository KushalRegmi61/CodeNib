# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence

from ..inventory import ManifestInventory
from ..models import DependencyRecord, ManifestRecord, ProjectRecord


@dataclass
class AdapterContext:
    """Mutable orchestration context shared across adapters.

    ``diagnostics`` is appended in place and ``records`` accumulates every
    discovered manifest so later ``dependencies()`` passes can join against
    the full inventory. Deliberately not frozen: the scanner fills it in.
    """

    inventory: ManifestInventory
    diagnostics: list[str]
    records: list[ManifestRecord] = field(default_factory=list)


class WorkspaceAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    manifest_names: tuple[str, ...]

    @classmethod
    def discover(cls, context: AdapterContext) -> Iterable[ManifestRecord]: ...

    @classmethod
    def create_projects(
        cls, records: Sequence[ManifestRecord], context: AdapterContext
    ) -> Iterable[ProjectRecord]: ...

    @classmethod
    def dependencies(
        cls,
        project: ProjectRecord,
        projects: Sequence[ProjectRecord],
        context: AdapterContext,
    ) -> Iterable[DependencyRecord]: ...


def parsed_record(
    *,
    context: AdapterContext,
    path: str,
    kind: str,
    adapter_id: str,
    adapter_version: str,
    data: Any,
    raw_sha256: str,
) -> ManifestRecord:
    return ManifestRecord(
        path=path,
        kind=kind,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        data=data if isinstance(data, dict) else {},
        raw_sha256=raw_sha256,
    )

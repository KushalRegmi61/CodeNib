# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json

from ..models import ManifestRecord
from .base import AdapterContext


class TurboAdapter:
    adapter_id = "turbo"
    adapter_version = "1"
    manifest_names = ("turbo.json",)

    @classmethod
    def discover(cls, context: AdapterContext):
        path = "turbo.json"
        if path not in context.inventory.raw:
            return []
        try:
            data = json.loads(context.inventory.raw[path].decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("turbo.json must be an object")
            return [
                ManifestRecord(
                    path=path,
                    kind="turbo_config",
                    adapter_id=cls.adapter_id,
                    adapter_version=cls.adapter_version,
                    raw_sha256=f"sha256:{hashlib.sha256(context.inventory.raw[path]).hexdigest()}",
                    data=data,
                )
            ]
        except ValueError as exc:
            context.diagnostics.append(f"{path}: {exc}")
            return []

    @classmethod
    def create_projects(cls, records, context):
        return []

    @classmethod
    def dependencies(cls, project, projects, context):
        return []

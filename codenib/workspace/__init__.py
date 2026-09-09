# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Language-agnostic workspace and project discovery."""

from .models import (DependencyRecord, ManifestRecord, ProjectRecord,
                     WorkspaceModel, WorkspaceScanBudget)
from .scanner import scan_workspace

__all__ = [
    "DependencyRecord",
    "ManifestRecord",
    "ProjectRecord",
    "WorkspaceModel",
    "WorkspaceScanBudget",
    "scan_workspace",
]

# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Typed structured-output contracts for the public MCP context tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExploreResponse(BaseModel):
    """Stable core envelope for the composed repository context response."""

    model_config = ConfigDict(extra="allow")

    context_response_schema_version: Literal[2] = 2
    query: str
    scope: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    project_context: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


__all__ = ["ExploreResponse"]

# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Shared request bounds for MCP tools that return repository context."""

from __future__ import annotations

from typing import Any

MAX_TOOL_RESULTS = 100
MAX_SEARCH_QUERY_CHARS = 16_000
MAX_REGEX_PATTERN_CHARS = 4_096
MAX_REGEX_FILTER_CHARS = 4_096
MAX_GRAPH_DEPTH = 8
MAX_DEPENDENCY_EDGES = 2_000
MAX_ROUTE_SYMBOLS = 32
MAX_LSP_POSITION = 2**31 - 1
MAX_LSP_SYMBOL_CHARS = 1_024
MAX_LSP_QUERY_CHARS = 16_000
MAX_LSP_ROUTE_TEXT_CHARS = 16_000
MAX_SOURCE_PATH_CHARS = 4_096
MAX_SOURCE_WINDOW_LINES = 200
MAX_SOURCE_CONTENT_CHARS = 16_000
MAX_EXPLORE_WINDOWS = 20
MAX_PROJECTS = 100
MAX_PROJECT_EVIDENCE = 200
MAX_PROJECT_ID_CHARS = 4_096


def required_text(
    value: Any,
    *,
    name: str,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty.")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters.")
    return value.strip()


def bounded_text(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    if len(value) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters.")
    return value


def bounded_integer(
    value: Any,
    *,
    name: str,
    minimum: int = 1,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def optional_project_id(value: Any) -> str | None:
    """Validate an optional persisted project identity."""

    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_PROJECT_ID_CHARS:
        raise ValueError("project_id must be a bounded string")
    normalized = value.strip()
    if not normalized.startswith("project://") or normalized == "project://":
        raise ValueError("project_id must use the project:// identity scheme")
    if "\\" in normalized or "\x00" in normalized:
        raise ValueError("project_id must be a canonical project identity")
    return normalized

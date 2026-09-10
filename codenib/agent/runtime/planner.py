# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Validation and trace integration for context-planner evidence packets.

The native Skill and subagents exchange bounded Markdown/YAML-shaped packets.
This module is deliberately an adapter, not a second planner store: validated
claims are written to the existing :class:`AgentRunTrace` and
:class:`ContextLedger` objects supplied by the caller.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .trace import AgentRunTrace, AgentTraceEvent

PLANNER_PACKET_VERSION = 1
PLANNER_LOGICAL_TOOLS = (
    "get_manifest",
    "explore_context",
    "search_context",
    "search_semantic",
    "search_bm25",
    "search_regex",
    "search_zoekt",
    "dependency_subgraph",
    "find_projects_using",
    "lsp_definition",
    "lsp_references",
    "lsp_route",
    "read_source",
)
PLANNER_AGENTS = frozenset(
    {"context-planner", "scope-search", "impact-navigator", "evidence-auditor"}
)
PLANNER_PACKET_STATUSES = frozenset(
    {"complete", "abstain", "budget_exhausted", "tool_unavailable"}
)
PLANNER_CLAIM_STATUSES = frozenset({"supported", "contradicted", "unresolved"})
PLANNER_AUTHORITIES = frozenset(
    {"live_source", "indexed_excerpt", "graph_fact", "manifest_fact"}
)
PLANNER_COVERAGE_PREFIXES = (
    "used",
    "skipped:",
    "deferred:budget",
    "unavailable:",
)
PLANNER_RECOMMENDATIONS = frozenset({"stop", "continue", "abstain"})

_PACKET_KEYS = frozenset(
    {
        "packet_version",
        "agent",
        "status",
        "question",
        "scope",
        "claims",
        "actions",
        "tool_coverage",
        "diagnostics",
        "gaps",
        "recommendation",
        "budget",
        "tool_calls",
        "delegations",
    }
)
_SCOPE_KEYS = frozenset({"project_id", "file_path", "source_identity"})
_CLAIM_KEYS = frozenset(
    {
        "evidence_id",
        "claim",
        "status",
        "authority",
        "citations",
        "source_verified",
        "confidence",
    }
)
_CITATION_KEYS = frozenset(
    {"file", "start_line", "end_line", "node_id", "edge_type"}
)
_BUDGET_KEYS = frozenset({"actions", "tool_calls", "delegations"})
_TOOL_CALL_KEYS = frozenset(
    {"logical_tool", "registered_tool", "status", "duration_ms", "error"}
)
_DELEGATION_KEYS = frozenset(
    {"agent", "question", "status", "budget", "packet_status"}
)


class PlannerPacketError(ValueError):
    """Raised when an untrusted planner packet violates its contract."""


@dataclass(frozen=True, slots=True)
class LiveToolBinding:
    """Result of matching logical CodeNib tools to one live host listing."""

    resolved: Mapping[str, str]
    missing: tuple[str, ...] = ()
    ambiguous: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        diagnostics = [f"unavailable:registration:{tool}" for tool in self.missing]
        diagnostics.extend(
            f"ambiguous:registration:{tool}:{','.join(candidates)}"
            for tool, candidates in self.ambiguous
        )
        return tuple(diagnostics)


def resolve_live_tool_names(
    registered_names: Iterable[str],
    *,
    logical_tools: Sequence[str] = PLANNER_LOGICAL_TOOLS,
) -> LiveToolBinding:
    """Resolve logical tools from the host's actual live MCP listing.

    Exact names win. Namespaced host registrations are matched by their final
    logical component (for example, a host may expose ``...__read_source``),
    but no server or clone-specific prefix is constructed or persisted.
    """

    names = tuple(dict.fromkeys(name for name in registered_names if name))
    resolved: dict[str, str] = {}
    missing: list[str] = []
    ambiguous: list[tuple[str, tuple[str, ...]]] = []
    for logical in logical_tools:
        exact = tuple(name for name in names if name == logical)
        candidates = exact or tuple(
            name for name in names if name.endswith(f"__{logical}")
        )
        if len(candidates) == 1:
            resolved[logical] = candidates[0]
        elif not candidates:
            missing.append(logical)
        else:
            ambiguous.append((logical, candidates))
    return LiveToolBinding(resolved, tuple(missing), tuple(ambiguous))


def deterministic_evidence_id(
    claim: str,
    citations: Sequence[Mapping[str, Any]],
    authority: str,
    source_identity: Any = None,
) -> str:
    """Return a stable evidence ID for one claim and its provenance."""

    canonical = {
        "claim": claim.strip(),
        "citations": [_canonical_json_value(citation) for citation in citations],
        "authority": authority,
        "source_identity": _canonical_json_value(source_identity),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:16]


def evidence_id_for_claim(
    claim: str,
    citations: Sequence[Mapping[str, Any]],
    authority: str,
    source_identity: Any = None,
) -> str:
    """Compatibility alias with a descriptive name for packet callers."""

    return deterministic_evidence_id(claim, citations, authority, source_identity)


def normalize_planner_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize an untrusted planner/subagent packet.

    Evidence IDs are always recomputed. The returned mapping contains only
    contract fields, so arbitrary model output cannot enter the trace or
    ledger as an opaque payload.
    """

    if not isinstance(packet, Mapping):
        raise PlannerPacketError("planner packet must be a mapping")
    unknown = set(packet) - _PACKET_KEYS
    required = _PACKET_KEYS - {"tool_calls", "delegations"}
    if unknown or not required.issubset(packet):
        raise PlannerPacketError(
            "planner packet keys must contain "
            + ", ".join(sorted(required))
        )
    _require_exact_int(packet["packet_version"], PLANNER_PACKET_VERSION, "packet_version")
    agent = _require_string(packet["agent"], "agent")
    if agent not in PLANNER_AGENTS:
        raise PlannerPacketError(f"unsupported planner agent: {agent}")
    status = _require_string(packet["status"], "status")
    if status not in PLANNER_PACKET_STATUSES:
        raise PlannerPacketError(f"unsupported planner packet status: {status}")
    question = _require_string(packet["question"], "question")
    scope = _normalize_scope(packet["scope"])
    claims = _normalize_claims(packet["claims"], scope.get("source_identity"))
    actions = _normalize_string_or_mapping_list(packet["actions"], "actions", 16)
    tool_coverage = _normalize_tool_coverage(packet["tool_coverage"])
    diagnostics = _normalize_string_list(packet["diagnostics"], "diagnostics", 64)
    gaps = _normalize_string_list(packet["gaps"], "gaps", 64)
    recommendation = _require_string(packet["recommendation"], "recommendation")
    if recommendation not in PLANNER_RECOMMENDATIONS:
        raise PlannerPacketError(f"unsupported planner recommendation: {recommendation}")
    budget = _normalize_budget(packet["budget"])
    normalized: dict[str, Any] = {
        "packet_version": PLANNER_PACKET_VERSION,
        "agent": agent,
        "status": status,
        "question": question,
        "scope": scope,
        "claims": claims,
        "actions": actions,
        "tool_coverage": tool_coverage,
        "diagnostics": diagnostics,
        "gaps": gaps,
        "recommendation": recommendation,
        "budget": budget,
    }
    if "tool_calls" in packet:
        normalized["tool_calls"] = _normalize_tool_calls(packet["tool_calls"])
    if "delegations" in packet:
        normalized["delegations"] = _normalize_delegations(packet["delegations"])
    return normalized


def record_planner_packet(
    trace: AgentRunTrace,
    packet: Mapping[str, Any],
    *,
    turn: int,
    parent_agent: str | None = None,
) -> dict[str, Any]:
    """Validate a packet and append bounded planner events/context entries."""

    if turn < 0:
        raise PlannerPacketError("turn must be non-negative")
    normalized = normalize_planner_packet(packet)
    agent = normalized["agent"]
    trace.add(
        "planner_action",
        turn,
        agent=agent,
        status=normalized["status"],
        recommendation=normalized["recommendation"],
        action_count=len(normalized["actions"]),
        budget=dict(normalized["budget"]),
        diagnostics_count=len(normalized["diagnostics"]),
        gaps_count=len(normalized["gaps"]),
        parent_agent=parent_agent,
    )
    for call in normalized.get("tool_calls", ()):
        trace.add("planner_tool_call", turn, agent=agent, **call)
    for logical_tool, decision in normalized["tool_coverage"].items():
        if decision.startswith("unavailable:"):
            trace.add(
                "tool_unavailable",
                turn,
                agent=agent,
                logical_tool=logical_tool,
                reason=decision.removeprefix("unavailable:") or "provider",
            )
    for delegation in normalized.get("delegations", ()):
        trace.add(
            "planner_delegation",
            turn,
            parent_agent=agent,
            **delegation,
        )
    source_identity = normalized["scope"].get("source_identity")
    for claim in normalized["claims"]:
        citation = claim["citations"][0] if claim["citations"] else None
        trace.add_context(
            source=f"planner:{agent}",
            state="available" if claim["status"] == "supported" else claim["status"],
            turn=turn,
            summary=claim["claim"],
            path=citation.get("file") if citation else None,
            entry_id=claim["evidence_id"],
            provenance={
                "authority": claim["authority"],
                "source_verified": claim["source_verified"],
                "source_identity": source_identity,
                "citations": claim["citations"],
            },
            freshness="verified" if claim["source_verified"] else "indexed",
            metadata={
                "claim_status": claim["status"],
                "confidence": claim["confidence"],
            },
        )
    return normalized


def ingest_planner_packet(
    trace: AgentRunTrace,
    packet: Mapping[str, Any],
    *,
    turn: int,
    parent_agent: str | None = None,
) -> dict[str, Any]:
    """Alias emphasizing that packets are ingested into existing runtime state."""

    return record_planner_packet(
        trace,
        packet,
        turn=turn,
        parent_agent=parent_agent,
    )


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlannerPacketError(f"{field} must be a non-empty string")
    return value.strip()


def _require_exact_int(value: Any, expected: int, field: str) -> None:
    if type(value) is not int or value != expected:
        raise PlannerPacketError(f"{field} must be {expected}")


def _normalize_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - _SCOPE_KEYS:
        raise PlannerPacketError(
            "scope must contain only project_id, file_path, and source_identity"
        )
    result = {}
    for key in ("project_id", "file_path"):
        item = value.get(key)
        if item is not None and not isinstance(item, str):
            raise PlannerPacketError(f"scope.{key} must be a string or null")
        result[key] = item.strip() if isinstance(item, str) else None
    # The source identity is optional in the public packet contract but is
    # retained when supplied by a live MCP result for stable claim IDs.
    if "source_identity" in value:
        result["source_identity"] = _canonical_json_value(value["source_identity"])
    return result


def _normalize_claims(value: Any, source_identity: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 64:
        raise PlannerPacketError("claims must be a list of at most 64 items")
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) - _CLAIM_KEYS:
            raise PlannerPacketError(f"claims[{index}] has invalid fields")
        claim = _require_string(raw.get("claim"), f"claims[{index}].claim")
        status = _require_string(raw.get("status"), f"claims[{index}].status")
        authority = _require_string(
            raw.get("authority"), f"claims[{index}].authority"
        )
        if status not in PLANNER_CLAIM_STATUSES:
            raise PlannerPacketError(f"claims[{index}].status is invalid")
        if authority not in PLANNER_AUTHORITIES:
            raise PlannerPacketError(f"claims[{index}].authority is invalid")
        citations = _normalize_citations(raw.get("citations"), index)
        source_verified = raw.get("source_verified")
        if type(source_verified) is not bool:
            raise PlannerPacketError(f"claims[{index}].source_verified must be boolean")
        confidence = _require_string(
            raw.get("confidence"), f"claims[{index}].confidence"
        )
        if confidence not in {"high", "medium", "low"}:
            raise PlannerPacketError(f"claims[{index}].confidence is invalid")
        result.append(
            {
                "evidence_id": deterministic_evidence_id(
                    claim, citations, authority, source_identity
                ),
                "claim": claim,
                "status": status,
                "authority": authority,
                "citations": citations,
                "source_verified": source_verified,
                "confidence": confidence,
            }
        )
    return result


def _normalize_citations(value: Any, claim_index: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 32:
        raise PlannerPacketError(f"claims[{claim_index}].citations is invalid")
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) - _CITATION_KEYS:
            raise PlannerPacketError(
                f"claims[{claim_index}].citations[{index}] has invalid fields"
            )
        file_path = raw.get("file")
        if not isinstance(file_path, str) or not file_path.strip():
            raise PlannerPacketError(
                f"claims[{claim_index}].citations[{index}].file is required"
            )
        normalized: dict[str, Any] = {"file": file_path.strip()}
        for key in ("start_line", "end_line"):
            line = raw.get(key)
            if line is not None and (type(line) is not int or line < 1):
                raise PlannerPacketError(
                    f"claims[{claim_index}].citations[{index}].{key} is invalid"
                )
            normalized[key] = line
        for key in ("node_id", "edge_type"):
            item = raw.get(key)
            if item is not None and not isinstance(item, str):
                raise PlannerPacketError(
                    f"claims[{claim_index}].citations[{index}].{key} is invalid"
                )
            normalized[key] = item
        result.append(normalized)
    return result


def _normalize_tool_coverage(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise PlannerPacketError("tool_coverage must be a mapping")
    expected = set(PLANNER_LOGICAL_TOOLS)
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        detail = f"missing={missing}" if missing else ""
        if extra:
            detail += f" extra={extra}"
        raise PlannerPacketError(f"tool_coverage must cover all logical tools ({detail})")
    result = {}
    for tool in PLANNER_LOGICAL_TOOLS:
        decision = _require_string(value[tool], f"tool_coverage.{tool}")
        if not decision.startswith(PLANNER_COVERAGE_PREFIXES):
            raise PlannerPacketError(f"tool_coverage.{tool} has invalid decision")
        result[tool] = decision
    return result


def _normalize_budget(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != _BUDGET_KEYS:
        raise PlannerPacketError("budget must contain actions, tool_calls, delegations")
    result = {}
    for key in _BUDGET_KEYS:
        item = value[key]
        if type(item) is not int or item < 0:
            raise PlannerPacketError(f"budget.{key} must be a non-negative integer")
        result[key] = item
    return result


def _normalize_string_list(value: Any, field: str, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise PlannerPacketError(f"{field} must be a list of at most {maximum} strings")
    return [_require_string(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _normalize_string_or_mapping_list(
    value: Any, field: str, maximum: int
) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise PlannerPacketError(f"{field} must be a bounded list")
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            result.append(item.strip())
        elif isinstance(item, Mapping):
            result.append(_canonical_json_value(item))
        else:
            raise PlannerPacketError(f"{field}[{index}] must be a string or mapping")
    return result


def _normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise PlannerPacketError("tool_calls must be a bounded list")
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) - _TOOL_CALL_KEYS:
            raise PlannerPacketError(f"tool_calls[{index}] has invalid fields")
        logical_tool = _require_string(raw.get("logical_tool"), "logical_tool")
        if logical_tool not in PLANNER_LOGICAL_TOOLS:
            raise PlannerPacketError(f"tool_calls[{index}] has unknown logical tool")
        registered_tool = _require_string(
            raw.get("registered_tool"), "registered_tool"
        )
        status = _require_string(raw.get("status"), "tool_calls.status")
        if status not in {"succeeded", "failed", "denied"}:
            raise PlannerPacketError(f"tool_calls[{index}].status is invalid")
        result.append(
            {
                "logical_tool": logical_tool,
                "registered_tool": registered_tool,
                "status": status,
                **{
                    key: raw[key]
                    for key in ("duration_ms", "error")
                    if key in raw
                },
            }
        )
    return result


def _normalize_delegations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 8:
        raise PlannerPacketError("delegations must be a bounded list")
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) - _DELEGATION_KEYS:
            raise PlannerPacketError(f"delegations[{index}] has invalid fields")
        agent = _require_string(raw.get("agent"), "delegations.agent")
        if agent not in PLANNER_AGENTS - {"context-planner"}:
            raise PlannerPacketError(f"delegations[{index}].agent is invalid")
        question = _require_string(raw.get("question"), "delegations.question")
        delegation_status = _require_string(raw.get("status"), "delegations.status")
        if delegation_status not in {"requested", "complete", "abstain"}:
            raise PlannerPacketError(f"delegations[{index}].status is invalid")
        result.append(
            {
                "agent": agent,
                "question": question,
                "status": delegation_status,
                **{key: raw[key] for key in ("budget", "packet_status") if key in raw},
            }
        )
    return result


__all__ = [
    "LiveToolBinding",
    "PLANNER_AGENTS",
    "PLANNER_CLAIM_STATUSES",
    "PLANNER_LOGICAL_TOOLS",
    "PLANNER_PACKET_STATUSES",
    "PLANNER_PACKET_VERSION",
    "PlannerPacketError",
    "deterministic_evidence_id",
    "evidence_id_for_claim",
    "ingest_planner_packet",
    "normalize_planner_packet",
    "record_planner_packet",
    "resolve_live_tool_names",
]

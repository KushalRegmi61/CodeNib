# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Context-planner packet validation and trace adapter tests."""

from __future__ import annotations

import pytest

from codenib.agent.runtime import (
    AgentRunTrace,
    PLANNER_LOGICAL_TOOLS,
    PlannerPacketError,
    deterministic_evidence_id,
    normalize_planner_packet,
    record_planner_packet,
    resolve_live_tool_names,
)


def _packet() -> dict[str, object]:
    return {
        "packet_version": 1,
        "agent": "context-planner",
        "status": "complete",
        "question": "Where is the context planner installed?",
        "scope": {
            "project_id": None,
            "file_path": "codenib/agent/instructions/context_planner/SKILL.md",
            "source_identity": {"commit": "abc123"},
        },
        "claims": [
            {
                "evidence_id": "model-supplied-id",
                "claim": "The Skill is packaged under the context planner instructions.",
                "status": "supported",
                "authority": "live_source",
                "citations": [
                    {
                        "file": "codenib/agent/instructions/context_planner/SKILL.md",
                        "start_line": 1,
                        "end_line": 10,
                        "node_id": None,
                        "edge_type": None,
                    }
                ],
                "source_verified": True,
                "confidence": "high",
            }
        ],
        "actions": ["verify source"],
        "tool_coverage": {
            tool: "used" if tool in {"get_manifest", "explore_context"} else "skipped:not_applicable"
            for tool in PLANNER_LOGICAL_TOOLS
        },
        "diagnostics": [],
        "gaps": [],
        "recommendation": "stop",
        "budget": {"actions": 2, "tool_calls": 2, "delegations": 0},
        "tool_calls": [
            {
                "logical_tool": "explore_context",
                "registered_tool": "mcp__live__explore_context",
                "status": "succeeded",
            }
        ],
        "delegations": [],
    }


def test_live_tool_resolution_uses_listing_without_guessing_prefix() -> None:
    binding = resolve_live_tool_names(
        ["mcp__checkout__get_manifest", "mcp__checkout__read_source"]
    )

    assert binding.resolved == {
        "get_manifest": "mcp__checkout__get_manifest",
        "read_source": "mcp__checkout__read_source",
    }
    assert "explore_context" in binding.missing
    assert binding.diagnostics[0].startswith("unavailable:registration:")


def test_evidence_ids_are_deterministic_and_source_bound() -> None:
    citations = [{"file": "src/app.py", "start_line": 1}]

    first = deterministic_evidence_id("claim", citations, "live_source", "abc")
    second = deterministic_evidence_id("claim", citations, "live_source", "abc")
    changed = deterministic_evidence_id("claim", citations, "live_source", "def")

    assert first == second
    assert first != changed
    assert first.startswith("ev_")


def test_packet_normalization_recomputes_evidence_and_records_existing_trace() -> None:
    packet = _packet()

    normalized = normalize_planner_packet(packet)
    assert normalized["claims"][0]["evidence_id"] != "model-supplied-id"

    trace = AgentRunTrace()
    result = record_planner_packet(trace, packet, turn=3)

    assert result == normalized
    assert [event.kind for event in trace.events] == [
        "planner_action",
        "planner_tool_call",
    ]
    assert trace.events[1].data["registered_tool"] == "mcp__live__explore_context"
    assert trace.context[0].entry_id == normalized["claims"][0]["evidence_id"]
    assert trace.context[0].provenance["source_identity"] == {"commit": "abc123"}
    assert trace.to_dict()["context"][0]["entry_id"].startswith("ev_")


def test_packet_validation_rejects_incomplete_tool_coverage() -> None:
    packet = _packet()
    del packet["tool_coverage"]["read_source"]  # type: ignore[index]

    with pytest.raises(PlannerPacketError, match="tool_coverage"):
        normalize_planner_packet(packet)


def test_packet_validation_rejects_unknown_mcp_logical_tool() -> None:
    packet = _packet()
    packet["tool_calls"] = [
        {
            "logical_tool": "mcp__clone__read_source",
            "registered_tool": "mcp__clone__read_source",
            "status": "succeeded",
        }
    ]

    with pytest.raises(PlannerPacketError, match="unknown logical tool"):
        normalize_planner_packet(packet)

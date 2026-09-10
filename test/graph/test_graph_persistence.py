# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

import pickle

from codenib.graph.code_graph import (
    CodeGraph,
    current_graph_schema_version,
    persisted_graph_schema_version,
)


def test_persisted_graph_schema_version_reads_current_graph_without_loading(tmp_path):
    path = tmp_path / "graph.pkl"
    CodeGraph().save_graph(path)

    assert persisted_graph_schema_version(path) == current_graph_schema_version()


def test_persisted_graph_schema_version_reports_old_and_invalid_pickles(tmp_path):
    old_path = tmp_path / "old.pkl"
    with old_path.open("wb") as handle:
        pickle.dump({"schema_version": 4, "graph": "not loaded"}, handle)
    invalid_path = tmp_path / "invalid.pkl"
    invalid_path.write_bytes(b"not a pickle")
    invalid_unicode_path = tmp_path / "invalid-unicode.pkl"
    invalid_unicode_path.write_bytes(b"\x8c\x01\xff.")

    assert persisted_graph_schema_version(old_path) == 4
    assert persisted_graph_schema_version(invalid_path) is None
    assert persisted_graph_schema_version(invalid_unicode_path) is None


def test_schema_six_graph_is_rejected_with_rebuild_guidance(tmp_path):
    path = tmp_path / "schema-six.pkl"
    with path.open("wb") as handle:
        pickle.dump({"schema_version": 6}, handle)

    try:
        CodeGraph.load_graph(path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("schema-6 graph unexpectedly loaded")

    assert "schema_version=6" in message
    assert "expected 7" in message
    assert "--rebuild" in message

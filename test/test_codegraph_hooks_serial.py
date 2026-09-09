# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codenib.codegraph_hooks import (
    HOOK_MARKER,
    HOOK_NAMES,
    hook_file_path,
    install_hooks,
    load_hook_receipt,
    remove_hooks,
)

pytestmark = pytest.mark.integration_serial


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, timeout=60
    )


@pytest.fixture()
def scratch_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "hooked"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@codenib.ai")
    _git(repo, "config", "user.name", "codenib-test")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    monkeypatch.setenv("CODENIB_HOOK_MODE", "sync")
    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))
    return repo


def test_hook_lifecycle_refreshes_index_on_commit(scratch_repo: Path) -> None:
    from codenib.cli import index_repository
    from codenib.paths import repo_state_dir

    index_repository(scratch_repo, languages=["python"], views=["bm25"])
    install_hooks(
        scratch_repo,
        mode="sync",
        batch_size=None,
        codenib_argv=("codenib",),
    )
    for name in HOOK_NAMES:
        content = hook_file_path(scratch_repo, name).read_text()
        assert HOOK_MARKER in content
    assert load_hook_receipt(scratch_repo) is not None

    (scratch_repo / "b.py").write_text("y = 2\n")
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-m", "second")

    manifest_path = repo_state_dir(scratch_repo) / "indexes" / "repo_manifest.json"
    assert manifest_path.is_file()

    remove_hooks(scratch_repo, force=False)
    assert load_hook_receipt(scratch_repo) is None

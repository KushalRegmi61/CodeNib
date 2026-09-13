# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for committed-tree (`--from-head`) indexing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required for from-head tests"
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return completed.stdout.strip()


@pytest.fixture()
def head_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "headrepo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@codenib.ai")
    _git(repo, "config", "user.name", "codenib-test")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


@git
def test_head_snapshot_matches_head_not_worktree(head_repo: Path) -> None:
    from codenib.compiler.head_snapshot import materialize_head_snapshot
    from codenib.repository_source_selection import RepositorySourceSelection
    from codenib.source_fingerprint import fingerprint_repository

    clean_fingerprint = fingerprint_repository(
        head_repo, selection=RepositorySourceSelection()
    ).value

    (head_repo / "a.py").write_text("x = DIRTY\n")
    (head_repo / "untracked.py").write_text("y = 2\n")
    dirty_fingerprint = fingerprint_repository(
        head_repo, selection=RepositorySourceSelection()
    ).value
    assert dirty_fingerprint != clean_fingerprint

    with materialize_head_snapshot(head_repo) as snapshot:
        assert (snapshot / "a.py").read_text() == "x = 1\n"
        assert not (snapshot / "untracked.py").exists()
        snapshot_fingerprint = fingerprint_repository(
            snapshot, selection=RepositorySourceSelection()
        ).value

    assert snapshot_fingerprint == clean_fingerprint


@git
def test_head_snapshot_ignores_hook_git_env(
    head_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    from codenib.compiler.head_snapshot import materialize_head_snapshot

    # Git exports GIT_DIR into hook processes; snapshot commands must not
    # inherit it when operating on another directory.
    monkeypatch.setenv("GIT_DIR", str(head_repo / ".git"))
    monkeypatch.chdir(tmp_path)

    with materialize_head_snapshot(head_repo) as snapshot:
        assert (snapshot / "a.py").read_text() == "x = 1\n"


@git
def test_head_snapshot_without_head_errors(tmp_path: Path) -> None:
    from codenib.compiler.head_snapshot import (
        HeadSnapshotError,
        materialize_head_snapshot,
    )

    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init")

    with pytest.raises(HeadSnapshotError):
        with materialize_head_snapshot(repo):
            pass


@git
def test_index_from_head_ignores_dirty_worktree(
    head_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    from codenib import cli
    from codenib.paths import repo_state_dir

    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))

    (head_repo / "a.py").write_text("x = DIRTY\n")
    (head_repo / "untracked.py").write_text("y = 2\n")
    head = _git(head_repo, "rev-parse", "HEAD")

    manifest, failed = cli.index_repository(
        head_repo, languages=["python"], views=["bm25"], from_head=True
    )

    assert failed == []
    assert manifest.commit == head
    assert manifest.index_is_current("bm25")
    assert Path(manifest.repo_path).resolve() == head_repo.resolve()

    state_dir = repo_state_dir(head_repo)
    documents = (state_dir / "indexes" / "bm25" / "documents.json").read_text()
    assert '"file": "a.py"' in documents
    assert "DIRTY" not in documents
    assert "untracked.py" not in documents


@git
def test_from_head_without_commits_errors(tmp_path: Path, monkeypatch) -> None:
    from codenib import cli
    from codenib.compiler.head_snapshot import HeadSnapshotError

    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init")

    with pytest.raises(HeadSnapshotError):
        cli.index_repository(repo, languages=["python"], views=["bm25"], from_head=True)


def test_index_parser_accepts_from_head() -> None:
    from codenib import cli

    parsed = cli.build_parser().parse_args(["index", ".", "--from-head"])
    assert parsed.from_head is True

    defaulted = cli.build_parser().parse_args(["index", "."])
    assert defaulted.from_head is False


def test_hook_script_and_currency_require_from_head(
    tmp_path: Path, monkeypatch
) -> None:
    from codenib.codegraph_hooks import (
        HOOK_NAMES,
        HookReceipt,
        _hook_current,
        install_hooks,
        render_hook_script,
    )

    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)

    receipt = install_hooks(
        repo,
        mode="background",
        batch_size=None,
        codenib_argv=("codenib",),
    )
    assert receipt.hooks == HOOK_NAMES
    from codenib.codegraph_hooks import hook_file_path

    content = hook_file_path(repo, "post-commit").read_text(encoding="utf-8")
    assert "--from-head" in content

    legacy = render_hook_script(
        ("codenib", "index", str(repo), "--preset", "auto"), repo, batch_size=None
    )
    assert "--from-head" not in legacy
    assert (
        _hook_current(
            legacy,
            HookReceipt(repo.resolve(), "background", None, HOOK_NAMES),
            "post-commit",
        )
        is False
    )

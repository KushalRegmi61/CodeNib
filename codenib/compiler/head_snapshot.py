# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Materialize the committed HEAD tree as a disposable detached worktree.

Committed-tree indexing scans this snapshot instead of the working tree, so
a dirty worktree (or untracked files) can neither block nor pollute a
hook-triggered rebuild. The snapshot is content-identical to a clean
checkout at HEAD: fingerprints, relative artifact paths, and git
observations behave exactly as they would on that checkout.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class HeadSnapshotError(RuntimeError):
    """A safe, user-actionable committed-tree snapshot failure."""


# Git exports GIT_DIR (and sometimes GIT_WORK_TREE/GIT_INDEX_FILE) into hook
# processes. A snapshot command that operates on another directory must not
# inherit them: env takes precedence over `-C`, so the worktree checkout
# would otherwise resolve the caller's git dir (".git/index: Not a directory").
_SANITIZED_GIT_ENV_VARS = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"}
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _SANITIZED_GIT_ENV_VARS
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def resolve_head_commit(repo: Path) -> str:
    """Return the HEAD commit hash or raise :class:`HeadSnapshotError`."""

    repository = Path(repo).expanduser()
    completed = _git(repository, "rev-parse", "HEAD")
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        raise HeadSnapshotError(
            f"cannot resolve HEAD for {repository}: "
            f"{completed.stderr.strip() or 'not a git checkout or no commits yet'}"
        )
    return head


@contextmanager
def materialize_head_snapshot(repo: Path) -> Iterator[Path]:
    """Yield a detached worktree at HEAD, removed on exit.

    The caller owns indexing the snapshot with the real repository's cache
    directory and rewriting ``manifest.repo_path`` afterwards; this helper
    only owns worktree lifecycle.
    """

    repository = Path(repo).expanduser().resolve()
    head = resolve_head_commit(repository)
    tmpdir = tempfile.mkdtemp(prefix="codenib-head-")
    snapshot = Path(tmpdir) / "snapshot"
    try:
        completed = _git(repository, "worktree", "add", "--detach", str(snapshot), head)
        if completed.returncode != 0:
            raise HeadSnapshotError(
                f"cannot materialize HEAD snapshot for {repository}: "
                f"{completed.stderr.strip()}"
            )
        logger.info("Materialized HEAD snapshot %s at %s", head[:12], snapshot)
        yield snapshot
    finally:
        completed = _git(repository, "worktree", "remove", "--force", str(snapshot))
        if completed.returncode != 0:
            logger.warning(
                "git worktree remove failed (%s); deleting directly",
                completed.stderr.strip(),
            )
            shutil.rmtree(snapshot, ignore_errors=True)
        _git(repository, "worktree", "prune")
        shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = [
    "HeadSnapshotError",
    "materialize_head_snapshot",
    "resolve_head_commit",
]

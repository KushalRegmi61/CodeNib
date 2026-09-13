# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Managed git-hook auto-update for the CodeGraph product path.

The repository owns its ``.git/hooks`` directory.  CodeNib writes only
POSIX sh hooks that carry :data:`HOOK_MARKER`, records the installation in
a per-checkout receipt, and refuses to overwrite or remove a hook whose
observed content has drifted.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .codegraph_onboarding import (
    CodeGraphOnboardingError,
    resolve_codenib_command,
)
from .paths import repo_state_dir

HOOK_NAMES = ("post-commit", "post-checkout", "post-merge", "post-rewrite")
HOOK_MARKER = "# managed by codenib hook"
HOOK_RECEIPT_SCHEMA = 2
HOOK_RECEIPT_DIRNAME = "codegraph"
HOOK_RECEIPT_FILENAME = "hooks.json"
HOOK_MODE_ENV = "CODENIB_HOOK_MODE"

# ``__PINNED_PYTHON__`` is replaced at install time with the interpreter that
# ran ``install_hooks`` so GUI git clients with a minimal PATH still work.
_PINNED_PYTHON_PLACEHOLDER = "__PINNED_PYTHON__"

_HOOK_MODES = ("background", "sync", "off")
_HOOK_INSTALLED_STATE = "installed"


class CodeGraphHookError(RuntimeError):
    """A safe, user-actionable hook installation failure."""


@dataclass(frozen=True, slots=True)
class HookReceipt:
    """CodeNib-owned evidence for safe idempotency and removal."""

    repository: Path
    mode: str
    batch_size: int | None
    hooks: tuple[str, ...]
    command: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": HOOK_RECEIPT_SCHEMA,
            "repository": str(self.repository),
            "mode": self.mode,
            "batch_size": self.batch_size,
            "command": list(self.command) if self.command is not None else None,
            "hooks": {name: {"state": _HOOK_INSTALLED_STATE} for name in self.hooks},
        }


@dataclass(frozen=True, slots=True)
class HookInspection:
    """Observed state of one managed git hook."""

    name: str
    installed: bool
    current: bool
    detail: str


def resolve_hook_mode(explicit: str | None) -> str:
    """Resolve the hook execution mode.

    An explicit flag wins over the ``CODENIB_HOOK_MODE`` environment
    variable, which wins over the ``"background"`` default.
    """

    raw = explicit if explicit is not None else os.environ.get(HOOK_MODE_ENV)
    if raw is None:
        return "background"
    value = raw.strip()
    if not value and explicit is None:
        return "background"
    if value not in _HOOK_MODES:
        raise CodeGraphHookError(f"invalid CodeGraph hook mode: {raw!r}")
    return value


def hook_file_path(repo: Path, name: str) -> Path:
    """Return the hook file for one git hook name."""

    if name not in HOOK_NAMES:
        raise CodeGraphHookError(f"unsupported git hook: {name}")
    return Path(repo).expanduser().resolve() / ".git" / "hooks" / name


def hook_receipt_path(repository: str | Path) -> Path:
    """Return the per-checkout hook receipt path."""

    return repo_state_dir(repository) / HOOK_RECEIPT_DIRNAME / HOOK_RECEIPT_FILENAME


def _pinned_python() -> str:
    """Return the current interpreter if shell-safe, else empty (safe fallback)."""

    executable = sys.executable or ""
    if not executable or re.search(r"[^a-zA-Z0-9/_.@:\-]", executable):
        return ""
    return executable


# Detached Python launcher run by the hook shell: ``nohup`` is missing from
# Git for Windows' MSYS shell, so Python detaches itself (setsid equivalent).
# The hook process returns immediately; the child appends to the hook log.
_DETACHED_LAUNCH = (
    '"$CODENIB_PYTHON" -c "import os,subprocess,sys;'
    "_cmd=sys.argv[1:];"
    "_log=os.environ.get('CODENIB_REBUILD_LOG');"
    "_out=open(_log,'a',buffering=1) if _log else subprocess.DEVNULL;"
    "subprocess.Popen(_cmd,start_new_session=True,stdout=_out,"
    'stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,close_fds=True)" '
)


def render_hook_script(
    codenib_argv: tuple[str, ...], repo: Path, batch_size: int | None
) -> str:
    """Render the POSIX sh body for one managed git hook."""

    argv = tuple(codenib_argv)
    if batch_size is not None:
        argv = (*argv, "--embedding-batch-size", str(batch_size))
    command = shlex.join(argv)
    repository = Path(repo).expanduser().resolve()
    state_dir = str(repo_state_dir(repository))
    return (
        "\n".join(
            [
                "#!/bin/sh",
                HOOK_MARKER,
                f"# {repository}",
                "# The pinned interpreter below is replaced at install time.",
                "(",
                "set -u",
                'if [ "${CODENIB_HOOK_MODE:-background}" = "off" ]; then',
                "    exit 0",
                "fi",
                'if [ "${CODENIB_HOOK_MODE:-background}" = "sync" ]; then',
                "    _CODENIB_SYNC=1",
                "fi",
                "# Skip during rebase/merge/cherry-pick to avoid blocking --continue.",
                "GIT_DIR=${GIT_DIR:-$(git rev-parse --git-dir 2>/dev/null)}",
                '[ -d "$GIT_DIR/rebase-merge" ] && exit 0',
                '[ -d "$GIT_DIR/rebase-apply" ] && exit 0',
                '[ -f "$GIT_DIR/MERGE_HEAD" ] && exit 0',
                '[ -f "$GIT_DIR/CHERRY_PICK_HEAD" ] && exit 0',
                "# Skip inside a linked worktree (git-dir != git-common-dir).",
                '_CODENIB_GITDIR=$(cd "$(git rev-parse --git-dir 2>/dev/null)"'
                " 2>/dev/null && pwd)",
                '_CODENIB_COMMONDIR=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)"'
                " 2>/dev/null && pwd)",
                'if [ -n "$_CODENIB_COMMONDIR" ] && '
                '[ "$_CODENIB_GITDIR" != "$_CODENIB_COMMONDIR" ]; then',
                "    exit 0",
                "fi",
                f"lock={shlex.quote(f'{state_dir}/.hook.lock')}",
                'if ! mkdir "$lock" 2>/dev/null; then',
                "    exit 0",
                "fi",
                "trap 'rmdir \"$lock\"' EXIT INT TERM",
                f"log={shlex.quote(f'{state_dir}/hook.log')}",
                'export CODENIB_REBUILD_LOG="$log"',
                "# Resolve a Python that can import codenib (GUI clients lack PATH).",
                '_PINNED="__PINNED_PYTHON__"',
                'CODENIB_PYTHON="$_PINNED"',
                'if [ -z "$CODENIB_PYTHON" ] || ! "$CODENIB_PYTHON" -c'
                ' "import importlib.util,sys;'
                " sys.exit(0 if importlib.util.find_spec('codenib') else 1)\""
                " 2>/dev/null; then",
                "    if command -v python3 >/dev/null 2>&1; then",
                '        CODENIB_PYTHON="python3"',
                "    else",
                '        CODENIB_PYTHON="python"',
                "    fi",
                "fi",
                'if [ "${_CODENIB_SYNC:-}" = "1" ]; then',
                f'    {command} >>"$log" 2>&1',
                "else",
                '    if [ -n "${CODENIB_HOOK_TIMEOUT:-}" ]'
                ' && [ "$CODENIB_HOOK_TIMEOUT" -eq "$CODENIB_HOOK_TIMEOUT" ]'
                ' 2>/dev/null && [ "$CODENIB_HOOK_TIMEOUT" -gt 0 ]; then',
                f'        {_DETACHED_LAUNCH}timeout "$CODENIB_HOOK_TIMEOUT"'
                f' {command} >>"$log" 2>&1',
                "    else",
                f'        {_DETACHED_LAUNCH}{command} >>"$log" 2>&1',
                "    fi",
                "fi",
                ")",
                "exit 0",
            ]
        )
        + "\n"
    )


def _resolve_hook_argv(codenib_argv: tuple[str, ...]) -> tuple[str, ...]:
    prefix = tuple(codenib_argv)
    try:
        if not prefix:
            resolved, extra = resolve_codenib_command(None)
        elif os.path.isabs(prefix[0]):
            resolved, extra = prefix[0], prefix[1:]
            if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
                raise CodeGraphHookError(
                    f"CodeNib command is not executable: {resolved}"
                )
        else:
            resolved, extra = resolve_codenib_command(prefix[0])
            extra = (*extra, *prefix[1:])
    except CodeGraphOnboardingError as exc:
        raise CodeGraphHookError(str(exc)) from exc
    return (resolved, *extra)


def _check_batch_size(batch_size: int | None) -> None:
    if batch_size is not None and (type(batch_size) is not int or batch_size <= 0):
        raise CodeGraphHookError(f"invalid CodeGraph hook batch size: {batch_size!r}")


def hook_runtime_supports_batch_size(
    command: Sequence[str], *, timeout: int = 10
) -> bool:
    """Whether ``<command> index --help`` advertises --embedding-batch-size."""

    try:
        completed = subprocess.run(
            [*command, "index", "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return False
    return completed.returncode == 0 and "--embedding-batch-size" in completed.stdout


def hook_runtime_supports_from_head(
    command: Sequence[str], *, timeout: int = 10
) -> bool:
    """Whether ``<command> index --help`` advertises --from-head."""

    try:
        completed = subprocess.run(
            [*command, "index", "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return False
    return completed.returncode == 0 and "--from-head" in completed.stdout


def install_hooks(
    repo: Path,
    *,
    mode: str,
    batch_size: int | None,
    codenib_argv: tuple[str, ...],
    force: bool = False,
    dry_run: bool = False,
) -> HookReceipt:
    """Install managed hooks and record a receipt.

    ``dry_run`` returns the would-be receipt without writing anything.
    """

    repository = Path(repo).expanduser().resolve()
    if mode not in _HOOK_MODES:
        raise CodeGraphHookError(f"invalid CodeGraph hook mode: {mode!r}")
    _check_batch_size(batch_size)
    if not (repository / ".git").is_dir():
        raise CodeGraphHookError(
            f"cannot install CodeGraph hooks: {repository} is not a git checkout"
        )
    command = _resolve_hook_argv(codenib_argv)
    if batch_size is not None and not hook_runtime_supports_batch_size(command):
        raise CodeGraphHookError(
            f"CodeGraph hook runtime {command[0]!r} does not support "
            "--embedding-batch-size; pass --command pointing at a codenib "
            "that supports the flag, or reinstall without --embedding-batch-size"
        )
    if not hook_runtime_supports_from_head(command):
        raise CodeGraphHookError(
            f"CodeGraph hook runtime {command[0]!r} does not support "
            "--from-head; pass --command pointing at a codenib "
            "that supports the flag (committed-tree hooks require it)"
        )
    argv = (*command, "index", str(repository), "--preset", "auto", "--from-head")
    script = render_hook_script(argv, repository, batch_size).replace(
        _PINNED_PYTHON_PLACEHOLDER, _pinned_python()
    )
    for name in HOOK_NAMES:
        path = hook_file_path(repository, name)
        if path.exists() or path.is_symlink():
            try:
                observed = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CodeGraphHookError(f"cannot read git hook {path}: {exc}") from exc
            if HOOK_MARKER not in observed and not force:
                raise CodeGraphHookError(
                    f"refusing to overwrite foreign git hook {path}; "
                    "pass force=True to replace it"
                )
    receipt = HookReceipt(repository, mode, batch_size, HOOK_NAMES, command)
    if dry_run:
        return receipt
    for name in HOOK_NAMES:
        path = hook_file_path(repository, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(script, encoding="utf-8")
            path.chmod(0o755)
        except OSError as exc:
            raise CodeGraphHookError(f"cannot write git hook {path}: {exc}") from exc
    write_hook_receipt(receipt)
    return receipt


def _hook_current(content: str, receipt: HookReceipt | None, name: str) -> bool:
    if receipt is None or name not in receipt.hooks:
        return False
    if "--from-head" not in content:
        return False
    if receipt.command is not None and shlex.join(receipt.command) not in content:
        return False
    if receipt.batch_size is None:
        return "--embedding-batch-size" not in content
    return (
        re.search(rf"--embedding-batch-size\s+{receipt.batch_size}(?!\d)", content)
        is not None
    )


def inspect_hooks(repo: Path, receipt: HookReceipt | None) -> list[HookInspection]:
    """Inspect the observed state of every managed git hook."""

    repository = Path(repo).expanduser().resolve()
    inspections: list[HookInspection] = []
    for name in HOOK_NAMES:
        path = hook_file_path(repository, name)
        if not path.exists():
            inspections.append(HookInspection(name, False, False, "not installed"))
            continue
        try:
            observed = path.read_text(encoding="utf-8")
        except OSError as exc:
            inspections.append(
                HookInspection(name, False, False, f"cannot read hook: {exc}")
            )
            continue
        if HOOK_MARKER not in observed:
            inspections.append(
                HookInspection(
                    name, False, False, "foreign hook present; use force to replace"
                )
            )
            continue
        if receipt is None:
            inspections.append(
                HookInspection(name, True, False, "installed without receipt")
            )
        elif _hook_current(observed, receipt, name):
            inspections.append(HookInspection(name, True, True, "current"))
        else:
            inspections.append(
                HookInspection(name, True, False, "hook content differs from receipt")
            )
    return inspections


def remove_hooks(repo: Path, *, force: bool = False) -> None:
    """Remove managed hooks and the hook receipt."""

    repository = Path(repo).expanduser().resolve()
    for name in HOOK_NAMES:
        path = hook_file_path(repository, name)
        if not path.exists() and not path.is_symlink():
            continue
        try:
            observed = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CodeGraphHookError(f"cannot read git hook {path}: {exc}") from exc
        if HOOK_MARKER not in observed and not force:
            raise CodeGraphHookError(
                f"refusing to remove foreign git hook {path}; "
                "pass force=True to remove it"
            )
    for name in HOOK_NAMES:
        hook_file_path(repository, name).unlink(missing_ok=True)
    hook_receipt_path(repository).unlink(missing_ok=True)


def _strict_string(value: object, *, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or any(character in value for character in "\x00\r\n")
    ):
        raise CodeGraphHookError(f"invalid CodeGraph hook receipt field: {field}")
    return value


def _strict_keys(
    value: object,
    expected: set[str],
    *,
    field: str,
) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise CodeGraphHookError(f"invalid CodeGraph hook receipt object: {field}")
    return value


def _strict_command(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if (
        type(value) is not list
        or not value
        or any(type(item) is not str or not item for item in value)
    ):
        raise CodeGraphHookError("invalid CodeGraph hook receipt field: command")
    return tuple(value)


def load_hook_receipt(repo: Path) -> HookReceipt | None:
    """Load and strictly validate the per-checkout hook receipt, if present."""

    repository = Path(repo).expanduser().resolve()
    path = hook_receipt_path(repository)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodeGraphHookError(
            f"cannot read CodeGraph hook receipt {path}: {exc}"
        ) from exc

    if type(payload) is not dict:
        raise CodeGraphHookError("invalid CodeGraph hook receipt object: root")
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version not in (
        1,
        HOOK_RECEIPT_SCHEMA,
    ):
        raise CodeGraphHookError("unsupported CodeGraph hook receipt schema")
    expected_keys = {"schema_version", "repository", "mode", "batch_size", "hooks"}
    if schema_version == HOOK_RECEIPT_SCHEMA:
        expected_keys = expected_keys | {"command"}
    root = _strict_keys(payload, expected_keys, field="root")
    recorded_repo = Path(_strict_string(root["repository"], field="repository"))
    if not recorded_repo.is_absolute() or recorded_repo != repository:
        raise CodeGraphHookError(
            "CodeGraph hook receipt repository does not match this checkout"
        )
    mode = _strict_string(root["mode"], field="mode")
    if mode not in _HOOK_MODES:
        raise CodeGraphHookError("invalid CodeGraph hook receipt field: mode")
    batch_size = root["batch_size"]
    if batch_size is not None and (type(batch_size) is not int or batch_size <= 0):
        raise CodeGraphHookError("invalid CodeGraph hook receipt field: batch_size")
    command: tuple[str, ...] | None = None
    if root["schema_version"] == HOOK_RECEIPT_SCHEMA:
        command = _strict_command(root["command"])
    hooks_value = root["hooks"]
    if type(hooks_value) is not dict or set(hooks_value) != set(HOOK_NAMES):
        raise CodeGraphHookError("invalid CodeGraph hook receipt object: hooks")
    for name, entry in hooks_value.items():
        if (
            type(entry) is not dict
            or set(entry) != {"state"}
            or entry["state"] != _HOOK_INSTALLED_STATE
        ):
            raise CodeGraphHookError(f"invalid CodeGraph hook receipt hook: {name}")
    return HookReceipt(
        repository,
        mode,
        batch_size,
        tuple(name for name in HOOK_NAMES if name in hooks_value),
        command,
    )


def write_hook_receipt(receipt: HookReceipt) -> Path:
    """Atomically write a private, deterministic hook receipt."""

    path = hook_receipt_path(receipt.repository)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (
        json.dumps(receipt.to_dict(), ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    )
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


__all__ = [
    "HOOK_MARKER",
    "HOOK_NAMES",
    "HOOK_RECEIPT_SCHEMA",
    "CodeGraphHookError",
    "HookInspection",
    "HookReceipt",
    "hook_file_path",
    "hook_receipt_path",
    "hook_runtime_supports_batch_size",
    "hook_runtime_supports_from_head",
    "inspect_hooks",
    "install_hooks",
    "load_hook_receipt",
    "remove_hooks",
    "render_hook_script",
    "resolve_hook_mode",
    "write_hook_receipt",
]

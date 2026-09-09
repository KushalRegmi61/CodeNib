# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for managed CodeGraph git hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from codenib.codegraph_hooks import (
    HOOK_MARKER,
    HOOK_NAMES,
    HOOK_RECEIPT_SCHEMA,
    CodeGraphHookError,
    HookInspection,
    HookReceipt,
    hook_file_path,
    hook_runtime_supports_batch_size,
    inspect_hooks,
    install_hooks,
    load_hook_receipt,
    remove_hooks,
    render_hook_script,
    resolve_hook_mode,
    write_hook_receipt,
)


def test_hook_parsers_wire_subcommands() -> None:
    from codenib import cli

    install = cli.build_parser().parse_args(
        [
            "codegraph",
            "hook",
            "install",
            ".",
            "--mode",
            "background",
            "--embedding-batch-size",
            "2",
        ]
    )
    assert install.codegraph_command == "hook"
    assert install.hook_command == "install"
    assert install.mode == "background"
    assert install.embedding_batch_size == 2

    status = cli.build_parser().parse_args(
        ["codegraph", "hook", "status", ".", "--json"]
    )
    assert status.hook_command == "status"
    assert status.json is True


def test_hook_install_parser_accepts_command() -> None:
    from codenib import cli

    parsed = cli.build_parser().parse_args(
        ["codegraph", "hook", "install", ".", "--command", sys.executable]
    )
    assert parsed.server_command == sys.executable

    defaulted = cli.build_parser().parse_args(["codegraph", "hook", "install", "."])
    assert defaulted.server_command is None

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["codegraph", "hook", "status", ".", "--command", sys.executable]
        )
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["codegraph", "hook", "uninstall", ".", "--command", sys.executable]
        )


def test_hook_install_handler_passes_explicit_command(
    tmp_path, monkeypatch, capsys
) -> None:
    from types import SimpleNamespace

    import codenib.codegraph_hooks as hooks
    from codenib import cli

    captured: dict = {}

    def fake_install(repo_path, *, mode, batch_size, codenib_argv, force, dry_run):
        captured["argv"] = codenib_argv
        return SimpleNamespace(hooks=("post-commit",), mode=mode)

    monkeypatch.setattr(hooks, "install_hooks", fake_install)
    args = SimpleNamespace(
        repo=str(tmp_path),
        mode=None,
        embedding_batch_size=None,
        server_command=sys.executable,
        force=False,
        dry_run=True,
    )

    assert cli._run_codegraph_hook_install(args) == 0
    assert captured["argv"][0].startswith(sys.executable)


def test_render_hook_script_contains_marker_and_command() -> None:
    script = render_hook_script(
        ("codenib", "index", "/repo", "--preset", "auto"),
        Path("/repo"),
        batch_size=2,
    )

    assert script.startswith("#!/bin/sh\n")
    assert HOOK_MARKER in script
    assert "--embedding-batch-size 2" in script
    assert "exit 0" in script.splitlines()[-1]


def test_render_hook_script_omits_batch_size_when_none() -> None:
    script = render_hook_script(("codenib",), Path("/repo"), batch_size=None)

    assert "--embedding-batch-size" not in script


def test_resolve_hook_mode_defaults_background(monkeypatch) -> None:
    monkeypatch.delenv("CODENIB_HOOK_MODE", raising=False)

    assert resolve_hook_mode(None) == "background"


def test_resolve_hook_mode_rejects_unknown() -> None:
    with pytest.raises(CodeGraphHookError):
        resolve_hook_mode("quantum")


def test_render_hook_script_gates_timeout_on_env_at_runtime() -> None:
    # The timeout branch is always rendered but only fires when the env
    # var is set and numeric — per-machine tuning without reinstall.
    script = render_hook_script(("codenib",), Path("/repo"), batch_size=None)

    assert "CODENIB_HOOK_TIMEOUT" in script
    assert script.count("timeout ") == 1


def _isolated_repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


def test_install_hooks_writes_hooks_and_receipt(tmp_path, monkeypatch) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)

    receipt = install_hooks(
        repo,
        mode="background",
        batch_size=None,
        codenib_argv=(sys.executable,),
    )

    assert receipt.mode == "background"
    assert receipt.batch_size is None
    assert receipt.to_dict()["schema_version"] == HOOK_RECEIPT_SCHEMA
    for name in HOOK_NAMES:
        content = hook_file_path(repo, name).read_text(encoding="utf-8")
        assert HOOK_MARKER in content
    assert load_hook_receipt(repo) is not None


def test_install_hooks_rejects_missing_git_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODENIB_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(CodeGraphHookError):
        install_hooks(
            repo, mode="background", batch_size=None, codenib_argv=(sys.executable,)
        )


def test_install_hooks_refuses_foreign_hook_without_force(
    tmp_path, monkeypatch
) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)
    hook_file_path(repo, "post-commit").write_text(
        "#!/bin/sh\necho foreign\n", encoding="utf-8"
    )

    with pytest.raises(CodeGraphHookError):
        install_hooks(
            repo, mode="background", batch_size=None, codenib_argv=(sys.executable,)
        )

    receipt = install_hooks(
        repo,
        mode="sync",
        batch_size=2,
        codenib_argv=(sys.executable, "-m", "codenib"),
        force=True,
    )
    assert receipt.mode == "sync"
    assert HOOK_MARKER in hook_file_path(repo, "post-commit").read_text(
        encoding="utf-8"
    )


def test_install_hooks_dry_run_writes_nothing(tmp_path, monkeypatch) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)

    receipt = install_hooks(
        repo,
        mode="background",
        batch_size=None,
        codenib_argv=(sys.executable,),
        dry_run=True,
    )

    assert receipt.mode == "background"
    assert load_hook_receipt(repo) is None
    assert not hook_file_path(repo, "post-commit").exists()


def test_hook_receipt_round_trip_and_repo_mismatch(tmp_path, monkeypatch) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)
    receipt = install_hooks(
        repo, mode="off", batch_size=4, codenib_argv=(sys.executable, "-m", "codenib")
    )

    loaded = load_hook_receipt(repo)
    assert loaded is not None
    assert loaded.to_dict() == receipt.to_dict()

    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    assert load_hook_receipt(other) is None


def test_load_hook_receipt_rejects_bad_schema(tmp_path, monkeypatch) -> None:
    from codenib.paths import repo_state_dir

    repo = _isolated_repo(tmp_path, monkeypatch)
    receipt_path = repo_state_dir(repo) / "codegraph" / "hooks.json"
    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "repository": str(repo.resolve()),
                "mode": "background",
                "batch_size": None,
                "hooks": {"post-commit": {"state": "installed"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CodeGraphHookError):
        load_hook_receipt(repo)


def test_inspect_and_remove_hooks(tmp_path, monkeypatch) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)

    assert all(
        item.installed is False for item in inspect_hooks(repo, load_hook_receipt(repo))
    )

    receipt = install_hooks(
        repo, mode="background", batch_size=None, codenib_argv=(sys.executable,)
    )
    inspections = inspect_hooks(repo, load_hook_receipt(repo))
    assert [item.name for item in inspections] == list(HOOK_NAMES)
    assert all(item.installed and item.current for item in inspections)
    assert all(isinstance(item, HookInspection) for item in inspections)
    assert receipt.to_dict()["hooks"].keys() == set(HOOK_NAMES)

    remove_hooks(repo)
    assert load_hook_receipt(repo) is None
    assert all(
        item.installed is False for item in inspect_hooks(repo, load_hook_receipt(repo))
    )


def test_write_hook_receipt_round_trip(tmp_path, monkeypatch) -> None:
    from codenib.paths import repo_state_dir

    repo = _isolated_repo(tmp_path, monkeypatch)
    receipt = HookReceipt(repo.resolve(), "sync", 2, ("post-commit",))

    path = write_hook_receipt(receipt)

    assert path == repo_state_dir(repo) / "codegraph" / "hooks.json"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert load_hook_receipt(repo) is not None
    assert load_hook_receipt(repo).to_dict() == receipt.to_dict()


def test_resolve_hook_mode_prefers_explicit_over_env(monkeypatch) -> None:
    monkeypatch.setenv("CODENIB_HOOK_MODE", "sync")

    assert resolve_hook_mode(None) == "sync"
    assert resolve_hook_mode("off") == "off"


def test_resolve_hook_mode_rejects_bad_env(monkeypatch) -> None:
    monkeypatch.setenv("CODENIB_HOOK_MODE", "quantum")

    with pytest.raises(CodeGraphHookError):
        resolve_hook_mode(None)


def test_render_hook_script_uses_portable_single_flight_lock() -> None:
    script = render_hook_script(("codenib",), Path("/repo"), batch_size=None)

    assert 'mkdir "$lock" 2>/dev/null' in script
    assert "flock" not in script


def test_hook_runtime_supports_batch_size_for_current_runtime() -> None:
    assert hook_runtime_supports_batch_size((sys.executable, "-m", "codenib")) is True


def test_hook_runtime_supports_batch_size_rejects_silent_runtime() -> None:
    assert hook_runtime_supports_batch_size(("/bin/true",)) is False


def test_hook_runtime_supports_batch_size_handles_missing_binary() -> None:
    assert hook_runtime_supports_batch_size(("/nonexistent-codenib-xyz",)) is False


def test_install_hooks_rejects_batch_size_for_stale_runtime(
    tmp_path, monkeypatch
) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)

    with pytest.raises(CodeGraphHookError, match="--embedding-batch-size"):
        install_hooks(
            repo,
            mode="background",
            batch_size=2,
            codenib_argv=("/bin/true",),
            dry_run=True,
        )


def test_install_hooks_without_batch_size_skips_runtime_probe(
    tmp_path, monkeypatch
) -> None:
    repo = _isolated_repo(tmp_path, monkeypatch)

    receipt = install_hooks(
        repo,
        mode="background",
        batch_size=None,
        codenib_argv=("/bin/true",),
        dry_run=True,
    )

    assert receipt.batch_size is None

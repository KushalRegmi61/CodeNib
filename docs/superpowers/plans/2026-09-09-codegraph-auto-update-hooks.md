# CodeGraph Auto-Update Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `codenib index` gains `--embedding-batch-size` so small GPUs can build the vector view, and `codenib codegraph hook install|status|uninstall` keeps indexes fresh across commits/pulls via detached git hooks.

**Architecture:** Thread the existing `embedding_batch_size` builder kwarg (runtime-only, identity-safe) up to the CLI; add a `codenib/codegraph_hooks.py` module mirroring `codegraph_onboarding.py` (render pure sh hook scripts, track a `hooks.json` receipt, drift-guard on uninstall); wire a `codegraph hook` subcommand family. Hooks call back into the `index` CLI — no new persistence, no daemon.

**Tech Stack:** Python 3.10+, argparse, POSIX sh hooks, pytest (unit default, `integration_serial` for repo-mutating test).

**Spec:** Approved design in chat 2026-09-09 (Phase 0+1 of the Graphify-parity program; Phase 2 skill-generator + Claude nudge is a separate follow-up plan). Maintenance contract: `docs/incremental_graph/index.md` (0.2.2 production gate — compiler reuses current views, rebuilds affected ones; no builder delta paths). Verified on GTX 1650 4GB: batch-2 builds the vector view in 47s; batch-64 OOMs.

## Global Constraints

- Python floor: 3.10 (`requires-python = >=3.10` in `pyproject.toml`).
- Commits use Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `ci`), imperative subjects at most 72 chars, no AI attribution footers.
- Format: black line length 88, isort, flake8+bugbear.
- New tests default to unit scope (no marker); repo-mutating tests get `integration_serial`.
- Storage guard: hooks live in the *target* repo's `.git/hooks/` (unversioned); all index state stays under `~/.codenib/repositories` (`CODENIB_HOME` relocatable). No new database, registry, or product route.
- Never break `codenib codegraph init|status|uninstall` behavior; never let a hook fail the user's git operation (hooks always exit 0; failures go to the hook log).

---

## File Map

- Modify `codenib/cli.py:2750-2773` (`_add_embedding_route_arguments` — add flag; covers `index` + `wiki` parsers automatically), `:510-562` (`_prepare_index_compiler` — accept + forward), `:565-611` (`index_repository` — accept + forward), `:645-694` (`_run_index` — validate + forward), wiki runner at `:1348-1362` (forward).
- Modify `codenib/compiler/index_builders.py` — no change needed (`register_default_builders` already accepts `embedding_batch_size`, `:2550`, forwards to `embedding_runtime_kwargs`, `:2584-2585`).
- Create `codenib/codegraph_hooks.py` — hook rendering, receipt, install/inspect/remove/status.
- Modify `codenib/cli.py:3131-3209` — add `codegraph hook` subparsers + handlers after the uninstall parser.
- Create `test/test_codegraph_hooks.py` — unit tests (no marker).
- Modify `test/test_cli.py` — batch-flag parser/validation tests (append near `test_index_parser_accepts_exact_source_exclusions`, `:68`).
- Create `test/test_codegraph_hooks_serial.py` — one `integration_serial` test (real git repo, sync mode).
- Modify `docs/codegraph.md` — new "Automatic updates" section after "Status and updates" (`:137-161`).

---

### Task 1: `--embedding-batch-size` CLI plumbing

**Files:**
- Modify: `codenib/cli.py:2750-2773`, `codenib/cli.py:510-562`, `codenib/cli.py:565-611`, `codenib/cli.py:645-694`, `codenib/cli.py:1348-1362`
- Test: `test/test_cli.py` (append after `:81`)

**Interfaces:**
- Consumes: `_optional_int(value, *, source: str) -> int | None` (`codenib/cli.py:71`), `register_default_builders(..., embedding_batch_size: Optional[int])`.
- Produces: `args.embedding_batch_size: int | None`; `index_repository(..., embedding_batch_size: Optional[int] = None)`; env fallback `CODENIB_EMBEDDING_BATCH_SIZE`. Precedence: flag > env > None.

- [ ] **Step 1: Write the failing parser test**

```python
def test_index_parser_accepts_embedding_batch_size() -> None:
    args = cli.build_parser().parse_args(
        ["index", ".", "--view", "vector", "--embedding-batch-size", "2"]
    )

    assert args.embedding_batch_size == 2


def test_index_parser_defaults_embedding_batch_size_to_none() -> None:
    args = cli.build_parser().parse_args(["index", "."])

    assert args.embedding_batch_size is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest test/test_cli.py::test_index_parser_accepts_embedding_batch_size test/test_cli.py::test_index_parser_defaults_embedding_batch_size -v -p no:cacheprovider`
Expected: FAIL with `AttributeError` / `SystemExit` (unrecognized argument).

- [ ] **Step 3: Add the flag to `_add_embedding_route_arguments`**

```python
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=None,
        help="encode batch size for local embedding builds (small GPUs: 2-4)",
    )
```

Insert after the `--embedding-api-key-env` argument (`codenib/cli.py:2770-2773`). This covers both `index` and `wiki` parsers since both call this helper.

- [ ] **Step 4: Thread through `_prepare_index_compiler`, `index_repository`, `_run_index`, wiki runner**

```python
# _prepare_index_compiler signature: add
    embedding_batch_size: int | None = None,
# and forward into register_default_builders(...):
        embedding_batch_size=embedding_batch_size,
```

```python
# index_repository signature: add
    embedding_batch_size: int | None = None,
# and forward into _prepare_index_compiler(...):
        embedding_batch_size=embedding_batch_size,
```

```python
# _run_index: resolve with flag > env > None, right after embedding_route is built:
    batch_size = _optional_int(
        getattr(args, "embedding_batch_size", None)
        or os.environ.get("CODENIB_EMBEDDING_BATCH_SIZE"),
        source="--embedding-batch-size",
    )
    if batch_size is not None and embedding_route is not None:
        if embedding_route.provider != "huggingface":
            raise CLIError(
                "--embedding-batch-size requires the huggingface embedding provider"
            )
    index_kwargs = {
        "languages": languages,
        "views": views,
        "source_selection": resolved_selection.selection,
        "rebuild": args.rebuild,
        "embedding_batch_size": batch_size,
    }
```

`_optional_int` already rejects non-integers, bools, and values <= 0 with `CLIError`. In the wiki runner (`:1348-1353`), add `"embedding_batch_size": batch_size` to `index_kwargs` using the same resolution (copy the 8-line block; the wiki path has its own `args`).

- [ ] **Step 5: Add validation tests**

```python
def test_embedding_batch_size_rejects_non_positive() -> None:
    with pytest.raises(cli.CLIError):
        cli._optional_int("0", source="--embedding-batch-size")
```

Plus a thread-through test using `SimpleNamespace` + monkeypatched `index_repository` asserting `embedding_batch_size=2` arrives (mirror existing `_run_index` tests in the file; keep the monkeypatch local to the test).

- [ ] **Step 6: Run new tests plus full `test_cli.py`**

Run: `.venv/bin/python -m pytest test/test_cli.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Manual proof on the 4GB GPU (replaces the throwaway script)**

Run: `.venv/bin/codenib index /home/pursottam/mine/projects/ai-saas-starter-kit --view vector --embedding-batch-size 2`
Expected: `vector fresh` in ~1 min, no OOM. (Do NOT commit this; it writes to `~/.codenib` only.)

- [ ] **Step 8: Commit**

```bash
git add codenib/cli.py test/test_cli.py
git commit -m "feat(cli): add --embedding-batch-size for small GPUs"
```

---

### Task 2: `codenib/codegraph_hooks.py` module

**Files:**
- Create: `codenib/codegraph_hooks.py`
- Test: `test/test_codegraph_hooks.py` (next task consumes this module)

**Interfaces:**
- Consumes: `resolve_codenib_command`, `codegraph_server_name`-style slug via `repository_state_key`, `repo_state_dir` (all from `codenib.codegraph_onboarding` / `codenib.paths`).
- Produces:
  - `HOOK_NAMES = ("post-commit", "post-checkout", "post-merge")`
  - `HOOK_MARKER = "# managed by codenib hook"`
  - `render_hook_script(codenib_argv: tuple[str, ...], repo: Path, batch_size: int | None) -> str`
  - `hook_file_path(repo: Path, name: str) -> Path` (under `<repo>/.git/hooks/<name>`)
  - `install_hooks(repo: Path, *, mode: str, batch_size: int | None, codenib_argv: tuple[str, ...], force: bool = False, dry_run: bool = False) -> HookReceipt` (`dry_run` returns the would-be receipt, writes nothing)
  - `inspect_hooks(repo: Path, receipt: HookReceipt | None) -> list[HookInspection]`
  - `remove_hooks(repo: Path, *, force: bool = False) -> None`
  - `write_hook_receipt(receipt: HookReceipt) -> Path`, `load_hook_receipt(repo: Path) -> HookReceipt | None`
  - `HookInspection` dataclass: `name: str`, `installed: bool`, `current: bool`, `detail: str`
  - `HookReceipt` dataclass with `to_dict()`; receipt file `<repo_state_dir>/codegraph/hooks.json`, `HOOK_RECEIPT_SCHEMA = 1`
  - `resolve_hook_mode(explicit: str | None) -> str`: `"background"` (default) | `"sync"` | `"off"`; explicit flag > `CODENIB_HOOK_MODE` env > `"background"`; anything else raises `CodeGraphHookError`.
  - `CodeGraphHookError(RuntimeError)`.

- [ ] **Step 1: Write failing tests for render + mode resolution**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks.py -v -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: No module named 'codenib.codegraph_hooks'` (create the empty test file first so collection works).

- [ ] **Step 3: Implement the module**

Requirements the implementation must satisfy (encode all of these; they are the contract, not suggestions):
1. SPDX header identical to `codegraph_onboarding.py:1-3`.
2. `render_hook_script` emits POSIX sh: `#!/bin/sh`, then `HOOK_MARKER` line, then a body that (a) exits 0 immediately when `CODENIB_HOOK_MODE=off`; (b) takes a `mkdir`-based lock under the repo state dir (`mkdir "$lock" 2>/dev/null || exit 0` — portable single-flight, no `flock` dependency); (c) runs the codenib argv with stdout/stderr appended to `<state_dir>/hook.log`; (d) always `exit 0` as the last line so git operations never fail because of us. Sync mode runs in foreground and propagates nothing (still `exit 0`); background mode uses `nohup ... >> log 2>&1 &`.
3. The codenib argv embedded in the script is `("<resolved-codenib>", "index", "<abs-repo>", "--preset", "auto")` plus `("--embedding-batch-size", str(n))` only when not None. Resolve the executable at install time via `resolve_codenib_command` (import from `codegraph_onboarding` — same rule as managed MCP registrations: absolute path recorded).
4. `install_hooks` raises `CodeGraphHookError` when `<repo>/.git` is absent (hooks need a git repo); refuses to overwrite a hook file lacking `HOOK_MARKER` unless `force=True` (drift guard mirroring `remove_client_registration`).
5. Receipt: `hooks.json` with `{"schema_version": 1, "repository": str, "mode": str, "batch_size": int | null, "hooks": {name: {"state": "installed"}}}`; strict validation on load mirroring `load_codegraph_receipt` (reject wrong schema, repo mismatch, non-string fields).
6. `CODENIB_HOOK_MODE` honored inside the script at run time (not bake-in), so one install serves all laptops; per-machine tuning needs no reinstall.
7. `CODENIB_HOOK_TIMEOUT` honored at run time: when set to a positive integer, wrap the codenib invocation as `timeout "$CODENIB_HOOK_TIMEOUT" <argv>`; unset/non-numeric means no timeout. Use a POSIX-safe numeric check:

```sh
if [ -n "${CODENIB_HOOK_TIMEOUT:-}" ] && [ "$CODENIB_HOOK_TIMEOUT" -eq "$CODENIB_HOOK_TIMEOUT" ] 2>/dev/null && [ "$CODENIB_HOOK_TIMEOUT" -gt 0 ]; then
    timeout "$CODENIB_HOOK_TIMEOUT" <argv> >>"$log" 2>&1
else
    <argv> >>"$log" 2>&1
fi
```

Reference implementation of the full script body (adapt paths/names exactly):

```sh
#!/bin/sh
# managed by codenib hook
# <repo>
set -u
if [ "${CODENIB_HOOK_MODE:-background}" = "off" ]; then
    exit 0
fi
lock="<state_dir>/.hook.lock"
if ! mkdir "$lock" 2>/dev/null; then
    exit 0
fi
trap 'rmdir "$lock"' EXIT INT TERM
log="<state_dir>/hook.log"
if [ "${CODENIB_HOOK_MODE:-background}" = "sync" ]; then
    <argv> >>"$log" 2>&1
else
    nohup <argv> >>"$log" 2>&1 &
fi
exit 0
```

`<argv>` is the shlex-joined codenib command; in background mode use the timeout conditional from bullet 7 around `<argv>`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks.py -v -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add codenib/codegraph_hooks.py test/test_codegraph_hooks.py
git commit -m "feat(codegraph): add hook render, receipt, and mode resolution"
```

---

### Task 3: `codegraph hook` CLI wiring

**Files:**
- Modify: `codenib/cli.py:3187-3209` (insert after the uninstall parser block)
- Test: `test/test_codegraph_hooks.py` (parser tests)

**Interfaces:**
- Consumes: Task 2 module; `resolve_repo_path`; existing `_codegraph_error` wrapper.
- Produces: `codenib codegraph hook {install,status,uninstall} [repo]` with `--mode {background,sync,off}`, `--embedding-batch-size N`, `--force`, `--dry-run`, `--json` (status only).

- [ ] **Step 1: Write failing parser tests**

```python
def test_hook_parsers_wire_subcommands() -> None:
    install = cli.build_parser().parse_args(
        ["codegraph", "hook", "install", ".", "--mode", "background",
         "--embedding-batch-size", "2"]
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks.py::test_hook_parsers_wire_subcommands -v -p no:cacheprovider`
Expected: FAIL with `SystemExit` (unrecognized `hook` subcommand).

- [ ] **Step 3: Add subparsers + handlers**

Insert after `codegraph_uninstall_parser.set_defaults(handler=_run_codegraph_uninstall)` (`:3209`):

```python
    codegraph_hook_parser = codegraph_subparsers.add_parser(
        "hook",
        help="keep CodeGraph indexes fresh across commits and pulls",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    hook_subparsers = codegraph_hook_parser.add_subparsers(
        dest="hook_command",
        required=True,
    )
    for hook_command in ("install", "status", "uninstall"):
        hook_parser = hook_subparsers.add_parser(
            hook_command,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        hook_parser.add_argument("repo", nargs="?", default=".")
        if hook_command in ("install", "status"):
            hook_parser.add_argument(
                "--mode",
                choices=("background", "sync", "off"),
                default=None,
                help="hook execution mode (default: CODENIB_HOOK_MODE or background)",
            )
        if hook_command == "install":
            hook_parser.add_argument(
                "--embedding-batch-size",
                type=int,
                default=None,
                help="encode batch size recorded into the hook command",
            )
            hook_parser.add_argument(
                "--force",
                action="store_true",
                help="overwrite hook files not installed by codenib",
            )
        if hook_command == "uninstall":
            hook_parser.add_argument(
                "--force",
                action="store_true",
                help="remove hook files even when their content has drifted",
            )
        if hook_command in ("install", "uninstall"):
            hook_parser.add_argument(
                "--dry-run",
                action="store_true",
                help="show hook changes without writing hook files or receipts",
            )
        if hook_command == "status":
            hook_parser.add_argument(
                "--json",
                action="store_true",
                help="print a machine-readable hook report",
            )
```

Handlers in the same file near `_run_codegraph_uninstall` (`:2661-2747`, mirror its resolve → try/except `(CodeGraphHookError, OSError)` → `raise _codegraph_error(exc)` structure):

```python
def _run_codegraph_hook_install(args: argparse.Namespace) -> int:
    from .codegraph_hooks import install_hooks
    from .codegraph_onboarding import resolve_codenib_command

    repo_path = resolve_repo_path(args.repo)
    batch_size = _optional_int(
        getattr(args, "embedding_batch_size", None)
        or os.environ.get("CODENIB_EMBEDDING_BATCH_SIZE"),
        source="--embedding-batch-size",
    )
    try:
        command, prefix = resolve_codenib_command()
        receipt = install_hooks(
            repo_path,
            mode=resolve_hook_mode(args.mode),
            batch_size=batch_size,
            codenib_argv=(command, *prefix),
            force=args.force,
        )
    except (CodeGraphHookError, OSError) as exc:
        raise _codegraph_error(exc) from exc
    if args.dry_run:  # install_hooks must accept dry_run and change nothing
        ...
```

Note: `install_hooks` needs a `dry_run: bool = False` parameter returning the receipt it *would* write — add it to the Task 2 signature (`dry_run` prints `would install <path>` per hook and skips writes). Status handler: human report (per-hook installed/current/drifted + receipt mode + last 5 lines of `hook.log` when present) or `--json` (`{"repository": str, "hooks": {name: {"installed": bool, "current": bool, "detail": str}}, "ready": bool}`); return 0 only when every receipt hook is installed and current, else 1. Uninstall handler: refuse drifted content without `--force` (mirror the `:2709-2713` drift refusal); remove receipt when no hooks remain (mirror `:2733-2737`); `--dry-run` prints `would remove` lines.

- [ ] **Step 4: Run hook tests**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add codenib/cli.py test/test_codegraph_hooks.py
git commit -m "feat(codegraph): add hook install, status, and uninstall"
```

---

### Task 4: `integration_serial` hook lifecycle test

**Files:**
- Create: `test/test_codegraph_hooks_serial.py`
- Mark: every test in the file `@pytest.mark.integration_serial` (installs hooks + commits in a repo — mutating by definition; never plain `integration`, which runs under xdist).

**Interfaces:**
- Consumes: Tasks 2–3 (`install_hooks`, hook script, `CODENIB_HOOK_MODE=sync`).
- Produces: proof that install → commit-triggered sync update → status → uninstall works on a scratch repo. Uses only `bm25` view (no model download) via a direct `index_repository(repo, languages=["python"], views=["bm25"], ...)` call for setup, then asserts the hook script triggers an index refresh.

- [ ] **Step 1: Write the test (sync mode, bm25 only, tmp_path repo)**

```python
# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from codenib import cli
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
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, timeout=60)


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

    index_repository(scratch_repo, languages=["python"], views=["bm25"])
    receipt = install_hooks(
        scratch_repo, mode="sync", batch_size=None,
        codenib_argv=("codenib",),
    )
    for name in HOOK_NAMES:
        content = hook_file_path(scratch_repo, name).read_text()
        assert HOOK_MARKER in content
    assert load_hook_receipt(scratch_repo) is not None

    (scratch_repo / "b.py").write_text("y = 2\n")
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-m", "second")

    manifest_path = (
        Path(os.environ["CODENIB_HOME"]) / "repositories"
        / f"{scratch_repo.name}-" / "indexes" / "repo_manifest.json"
    )
    # resolve via repo_state_dir instead of guessing the slug:
    from codenib.paths import repo_state_dir
    manifest_path = repo_state_dir(scratch_repo) / "indexes" / "repo_manifest.json"
    assert manifest_path.is_file()

    remove_hooks(scratch_repo, force=False)
    assert load_hook_receipt(scratch_repo) is None
```

Note: the stray `manifest_path` first assignment is dead code — delete it before committing; the `repo_state_dir` resolution is the real assertion path. (Left visible here so the worker sees both and removes the wrong one.)

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks_serial.py -v -p no:cacheprovider -m integration_serial`
Expected: PASS. The `post-commit` hook fires during the `second` commit in `sync` mode and refreshes bm25; no model download occurs (bm25 only).

- [ ] **Step 3: Commit**

```bash
git add test/test_codegraph_hooks_serial.py
git commit -m "test(codegraph): cover hook lifecycle on a scratch repo"
```

---

### Task 5: Docs — "Automatic updates" in `docs/codegraph.md`

**Files:**
- Modify: `docs/codegraph.md` (insert after "Status and updates", `:137-161`)
- Test: none (prose); verify with `mkdocs build --strict` only if the dev env has mkdocs (part of `dev` extra).

**Interfaces:**
- Consumes: Tasks 1–3 behavior.
- Produces: accurate operator docs incl. per-machine tuning and limitations.

- [ ] **Step 1: Add the section**

```markdown
## Automatic updates

Keep indexes fresh across commits, pulls, and branch switches without a
daemon. Install detached git hooks into the target checkout (`.git/hooks/`
is never committed, so reinstall per clone):

```bash
codenib codegraph hook install /path/to/repository \
  --embedding-batch-size 2
codenib codegraph hook status /path/to/repository
```

`post-commit`, `post-merge`, and `post-checkout` each trigger
`codenib index <repo> --preset auto` in the background and always exit 0,
so a slow or failed rebuild never blocks your git operation. When nothing
changed, the currency fast-path no-ops in about a second. Tune per machine
without reinstalling:

| Variable | Meaning | Default |
| --- | --- | --- |
| `CODENIB_HOOK_MODE` | `background`, `sync`, or `off` | `background` |
| `CODENIB_HOOK_TIMEOUT` | kill the background rebuild after N seconds | unset (no timeout) |
| `CODENIB_EMBEDDING_BATCH_SIZE` | fallback encode batch size (small GPUs: `2`) | model default |

Hooks refuse to overwrite hook files they did not write (use `--force`),
and `codenib codegraph hook uninstall` removes only CodeNib-managed hooks.
Indexes and MCP registrations are preserved. A dirty tree keeps views
`stale` by design — commit first, then let the hook rebuild.
```

Verify `CODENIB_HOOK_TIMEOUT` behavior matches Task 2 bullet 7 before documenting. Do not document what does not exist.

- [ ] **Step 2: Verify docs build (if mkdocs available)**

Run: `.venv/bin/python -c "import mkdocs" 2>/dev/null && .venv/bin/mkdocs build --strict -q || echo "mkdocs not installed; skipping"`
Expected: build success or clean skip.

- [ ] **Step 3: Commit**

```bash
git add docs/codegraph.md
git commit -m "docs(codegraph): document automatic index updates via hooks"
```

---

### Task 6: Full verification + dogfood

**Files:** none (verification only).

- [ ] **Step 1: Run the unit tier**

Run: `.venv/bin/python -m pytest -m "not slow and not integration and not integration_serial and not integration_serial_consumer" -x -q --tb=short -p no:cacheprovider`
Expected: 0 failures. (Known pre-existing env failure as of 2026-09-09: `test_captured_directory.py::test_owned_path_build_captures_and_isolates_arbitrary_partial_files` under `umask 0002`; re-run that single test with `umask 022` to confirm it is environmental, do not "fix" it in this plan.)

- [ ] **Step 2: Run the new serial test**

Run: `.venv/bin/python -m pytest test/test_codegraph_hooks_serial.py -q -p no:cacheprovider -m integration_serial`
Expected: PASS.

- [ ] **Step 3: Dogfood on ai-saas-starter-kit**

```bash
.venv/bin/codenib codegraph hook install /home/pursottam/mine/projects/ai-saas-starter-kit --embedding-batch-size 2 --dry-run
.venv/bin/codenib codegraph hook install /home/pursottam/mine/projects/ai-saas-starter-kit --embedding-batch-size 2
.venv/bin/codenib codegraph hook status /home/pursottam/mine/projects/ai-saas-starter-kit --json
```

Expected: dry-run changes nothing; install writes 3 hook files + receipt; status JSON reports all current. Then uninstall to leave the user's repo untouched:

```bash
.venv/bin/codenib codegraph hook uninstall /home/pursottam/mine/projects/ai-saas-starter-kit
```

- [ ] **Step 4: Lint the touched Python**

Run: `.venv/bin/python -m black --check codenib/cli.py codenib/codegraph_hooks.py test/test_codegraph_hooks.py test/test_codegraph_hooks_serial.py test/test_cli.py; .venv/bin/python -m isort --profile black --check-only --diff codenib/cli.py codenib/codegraph_hooks.py; .venv/bin/python -m flake8 codenib/cli.py codenib/codegraph_hooks.py`
Expected: clean (line length 88). Fix, don't silence.

---

## Out of scope (follow-up Plan 2)

`codenib install` skill/instruction generator (`AGENTS.md`, `.claude/skills/`, Cursor rules) and the Claude PreToolUse nudge. Opencode steering stays skills-files + MCP entry. Strict-block mode is explicitly excluded from v1.

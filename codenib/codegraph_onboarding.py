# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

"""Managed MCP client registration for the CodeGraph product path.

The client applications own their configuration files.  CodeNib invokes their
public CLIs, records only the registration it created, and refuses to overwrite
or remove a registration whose observed command has drifted.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ._version import package_version
from .paths import repo_state_dir, repository_state_key

CODEGRAPH_CLIENTS = ("codex", "claude")
CODEGRAPH_RECEIPT_SCHEMA = 3
_SUPPORTED_RECEIPT_SCHEMAS = frozenset({1, 2, CODEGRAPH_RECEIPT_SCHEMA})
CODEGRAPH_RECEIPT_DIRNAME = "codegraph"
CODEGRAPH_RECEIPT_FILENAME = "agent-integrations.json"

CONTEXT_PLANNER_SKILL_PATH = Path(".claude/skills/context-planner/SKILL.md")
CONTEXT_PLANNER_CLAUDE_PATH = Path(".claude/CLAUDE.md")
CONTEXT_PLANNER_ASSET_PATHS = (
    Path(".claude/skills/context-planner/references/mcp-routing.md"),
    Path(".claude/skills/context-planner/references/packet-contract.md"),
    Path(".claude/skills/context-planner/references/failure-modes.md"),
    Path(".claude/agents/scope-search.md"),
    Path(".claude/agents/impact-navigator.md"),
    Path(".claude/agents/evidence-auditor.md"),
)
CONTEXT_PLANNER_MANAGED_PATHS = (
    CONTEXT_PLANNER_SKILL_PATH,
    *CONTEXT_PLANNER_ASSET_PATHS,
    CONTEXT_PLANNER_CLAUDE_PATH,
)
CONTEXT_PLANNER_MARKER_START = "<!-- codenib:context-planner:start -->"
CONTEXT_PLANNER_MARKER_END = "<!-- codenib:context-planner:end -->"

_CLIENT_SCOPES = {"codex": "user", "claude": "local"}
_CLIENT_STATES = frozenset({"pending", "configured"})
_SERVER_NAME_PART = re.compile(r"[^a-z0-9._-]+")


class CodeGraphOnboardingError(RuntimeError):
    """A safe, user-actionable onboarding failure."""


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """One stdio server registration shared by supported agent clients."""

    name: str
    command: str
    args: tuple[str, ...]

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.command, *self.args)


@dataclass(frozen=True, slots=True)
class ManagedClient:
    """One client registration retained in the CodeNib receipt."""

    name: str
    scope: str
    state: str


@dataclass(frozen=True, slots=True)
class ManagedPlannerFile:
    """One additional planner asset tracked for safe refresh and removal."""

    path: str
    sha256: str
    created: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "created": self.created,
        }


@dataclass(frozen=True, slots=True)
class ContextPlannerInstallation:
    """Receipt data for files CodeNib installed into one project."""

    skill_sha256: str
    claude_block_sha256: str
    skill_created: bool
    claude_created: bool
    managed_files: tuple[ManagedPlannerFile, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "skill_path": CONTEXT_PLANNER_SKILL_PATH.as_posix(),
            "skill_sha256": self.skill_sha256,
            "claude_path": CONTEXT_PLANNER_CLAUDE_PATH.as_posix(),
            "claude_block_sha256": self.claude_block_sha256,
            "skill_created": self.skill_created,
            "claude_created": self.claude_created,
        }
        if self.managed_files:
            payload["managed_files"] = [
                item.to_dict() for item in self.managed_files
            ]
        return payload


@dataclass(frozen=True, slots=True)
class ContextPlannerInspection:
    """Current state of the project-local context-planner installation."""

    state: str
    skill_state: str
    claude_state: str
    detail: str
    asset_states: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ContextPlannerInstallPlan:
    """Conflict-checked file actions for one planner installation."""

    installation: ContextPlannerInstallation
    skill_action: str
    claude_action: str
    asset_actions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CodeGraphReceipt:
    """CodeNib-owned evidence for safe idempotency and removal."""

    repository: Path
    server: MCPServerSpec
    clients: tuple[ManagedClient, ...]
    context_planner: ContextPlannerInstallation | None = None

    def client(self, name: str) -> ManagedClient | None:
        return next((item for item in self.clients if item.name == name), None)

    def with_client(self, name: str, *, state: str) -> "CodeGraphReceipt":
        if name not in CODEGRAPH_CLIENTS:
            raise CodeGraphOnboardingError(f"unsupported agent client: {name}")
        if state not in _CLIENT_STATES:
            raise CodeGraphOnboardingError(f"invalid client receipt state: {state}")
        retained = [item for item in self.clients if item.name != name]
        retained.append(ManagedClient(name, _CLIENT_SCOPES[name], state))
        retained.sort(key=lambda item: CODEGRAPH_CLIENTS.index(item.name))
        return CodeGraphReceipt(
            self.repository,
            self.server,
            tuple(retained),
            self.context_planner,
        )

    def with_context_planner(
        self, installation: ContextPlannerInstallation
    ) -> "CodeGraphReceipt":
        return CodeGraphReceipt(
            self.repository,
            self.server,
            self.clients,
            installation,
        )

    def without_client(self, name: str) -> "CodeGraphReceipt":
        return CodeGraphReceipt(
            self.repository,
            self.server,
            tuple(item for item in self.clients if item.name != name),
            self.context_planner,
        )

    def without_context_planner(self) -> "CodeGraphReceipt":
        return CodeGraphReceipt(self.repository, self.server, self.clients, None)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CODEGRAPH_RECEIPT_SCHEMA,
            "repository": str(self.repository),
            "server": {
                "name": self.server.name,
                "command": self.server.command,
                "args": list(self.server.args),
            },
            "clients": {
                item.name: {"scope": item.scope, "state": item.state}
                for item in self.clients
            },
            "context_planner": (
                self.context_planner.to_dict()
                if self.context_planner is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ClientInspection:
    """Observed state of one native client registration."""

    client: str
    installed: bool
    exists: bool
    matches: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ServerCommandInspection:
    """Observed startup command identity for the managed MCP server."""

    ready: bool
    detail: str


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
FindCommand = Callable[[str], str | None]


def codegraph_receipt_path(repository: str | Path) -> Path:
    """Return the per-checkout onboarding receipt path."""

    return (
        repo_state_dir(repository)
        / CODEGRAPH_RECEIPT_DIRNAME
        / CODEGRAPH_RECEIPT_FILENAME
    )


def codegraph_server_name(repository: str | Path) -> str:
    """Return a readable, bounded, collision-resistant MCP server name."""

    key = repository_state_key(repository)
    slug, separator, digest = key.rpartition("-")
    if not separator:
        slug, digest = "repository", key[-12:]
    slug = _SERVER_NAME_PART.sub("-", slug.lower()).strip("-._")[:32]
    return f"codenib-{slug or 'repository'}-{digest}"


def resolve_codenib_command(
    explicit: str | None = None,
    *,
    which: FindCommand = shutil.which,
) -> tuple[str, tuple[str, ...]]:
    """Resolve a durable command used by the agent to start CodeNib."""

    if explicit is not None:
        value = explicit.strip()
        if not value or any(character in value for character in "\x00\r\n"):
            raise CodeGraphOnboardingError(
                "--command must be a non-empty single-line executable"
            )
        resolved = which(value)
        if resolved is None:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                raise CodeGraphOnboardingError(
                    f"CodeNib command is not executable or on PATH: {value}"
                )
            resolved = str(candidate)
        if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
            raise CodeGraphOnboardingError(
                f"CodeNib command is not executable: {resolved}"
            )
        return os.path.abspath(resolved), ()

    resolved = which("codenib")
    if resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK):
        return os.path.abspath(resolved), ()
    return os.path.abspath(sys.executable), ("-m", "codenib")


def make_server_spec(
    repository: str | Path,
    *,
    command: str,
    command_prefix: Sequence[str] = (),
) -> MCPServerSpec:
    """Build the exact full-surface MCP command for one checkout."""

    repo = Path(repository).expanduser().resolve()
    if any(character in str(repo) for character in "\x00\r\n"):
        raise CodeGraphOnboardingError(
            "CodeGraph repository paths must not contain control line breaks"
        )
    return MCPServerSpec(
        name=codegraph_server_name(repo),
        command=command,
        args=tuple(command_prefix) + ("mcp", str(repo), "--tool-surface", "full"),
    )


def resolve_requested_clients(
    requested: Sequence[str] | None,
    *,
    which: FindCommand = shutil.which,
) -> tuple[str, ...]:
    """Resolve explicit or auto-detected clients in stable order."""

    values = tuple(requested or ("auto",))
    unknown = sorted(set(values) - {"auto", *CODEGRAPH_CLIENTS})
    if unknown:
        raise CodeGraphOnboardingError(
            f"unsupported agent client: {', '.join(unknown)}"
        )
    if "auto" in values and len(values) != 1:
        raise CodeGraphOnboardingError(
            "--agent auto cannot be combined with an explicit client"
        )
    selected = (
        tuple(client for client in CODEGRAPH_CLIENTS if which(client) is not None)
        if values == ("auto",)
        else tuple(client for client in CODEGRAPH_CLIENTS if client in values)
    )
    if not selected:
        raise CodeGraphOnboardingError(
            "no supported agent client was found; install Codex or Claude Code, "
            "or pass --agent after its CLI is available"
        )
    missing = [client for client in selected if which(client) is None]
    if missing:
        raise CodeGraphOnboardingError(
            f"agent client command not found: {', '.join(missing)}"
        )
    return selected


def _strict_string(value: object, *, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or any(character in value for character in "\x00\r\n")
    ):
        raise CodeGraphOnboardingError(
            f"invalid CodeGraph integration receipt field: {field}"
        )
    return value


def _strict_keys(
    value: object,
    expected: set[str],
    *,
    field: str,
) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise CodeGraphOnboardingError(
            f"invalid CodeGraph integration receipt object: {field}"
        )
    return value


def _strict_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise CodeGraphOnboardingError(
            f"invalid CodeGraph integration receipt field: {field}"
        )
    return value


def _strict_sha256(value: object, *, field: str) -> str:
    result = _strict_string(value, field=field)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise CodeGraphOnboardingError(
            f"invalid CodeGraph integration receipt field: {field}"
        )
    return result


def _parse_context_planner_receipt(
    value: object,
) -> ContextPlannerInstallation | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt object: context_planner"
        )
    required_keys = {
        "skill_path",
        "skill_sha256",
        "claude_path",
        "claude_block_sha256",
        "skill_created",
        "claude_created",
    }
    optional_keys = {"managed_files"}
    if not set(value).issubset(required_keys | optional_keys) or not required_keys.issubset(
        value
    ):
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt object: context_planner"
        )
    item: Mapping[str, object] = value
    if item["skill_path"] != CONTEXT_PLANNER_SKILL_PATH.as_posix():
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt field: context_planner.skill_path"
        )
    if item["claude_path"] != CONTEXT_PLANNER_CLAUDE_PATH.as_posix():
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt field: context_planner.claude_path"
        )
    managed_files: list[ManagedPlannerFile] = []
    raw_managed_files = item.get("managed_files", [])
    if type(raw_managed_files) is not list:
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt field: context_planner.managed_files"
        )
    allowed_paths = {path.as_posix() for path in CONTEXT_PLANNER_ASSET_PATHS}
    for index, raw_file in enumerate(raw_managed_files):
        entry = _strict_keys(
            raw_file,
            {"path", "sha256", "created"},
            field=f"context_planner.managed_files[{index}]",
        )
        path = _strict_string(
            entry["path"],
            field=f"context_planner.managed_files[{index}].path",
        )
        if path not in allowed_paths:
            raise CodeGraphOnboardingError(
                "invalid CodeGraph integration receipt field: "
                f"context_planner.managed_files[{index}].path"
            )
        managed_files.append(
            ManagedPlannerFile(
                path=path,
                sha256=_strict_sha256(
                    entry["sha256"],
                    field=f"context_planner.managed_files[{index}].sha256",
                ),
                created=_strict_bool(
                    entry["created"],
                    field=f"context_planner.managed_files[{index}].created",
                ),
            )
        )
    if len({item.path for item in managed_files}) != len(managed_files):
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt object: duplicate managed file"
        )
    return ContextPlannerInstallation(
        skill_sha256=_strict_sha256(
            item["skill_sha256"], field="context_planner.skill_sha256"
        ),
        claude_block_sha256=_strict_sha256(
            item["claude_block_sha256"],
            field="context_planner.claude_block_sha256",
        ),
        skill_created=_strict_bool(
            item["skill_created"], field="context_planner.skill_created"
        ),
        claude_created=_strict_bool(
            item["claude_created"], field="context_planner.claude_created"
        ),
        managed_files=tuple(managed_files),
    )


def load_codegraph_receipt(repository: str | Path) -> CodeGraphReceipt | None:
    """Load and strictly validate the per-checkout receipt, if present."""

    repo = Path(repository).expanduser().resolve()
    path = codegraph_receipt_path(repo)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodeGraphOnboardingError(
            f"cannot read CodeGraph integration receipt {path}: {exc}"
        ) from exc

    if type(payload) is not dict:
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt object: root"
        )
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version not in _SUPPORTED_RECEIPT_SCHEMAS
    ):
        raise CodeGraphOnboardingError(
            "unsupported CodeGraph integration receipt schema"
        )
    expected_root_keys = {"schema_version", "repository", "server", "clients"}
    if schema_version >= 2:
        expected_root_keys.add("context_planner")
    root = _strict_keys(payload, expected_root_keys, field="root")
    recorded_repo = Path(
        _strict_string(root["repository"], field="repository")
    ).expanduser()
    if not recorded_repo.is_absolute() or recorded_repo != repo:
        raise CodeGraphOnboardingError(
            "CodeGraph integration receipt repository does not match this checkout"
        )

    server_value = _strict_keys(
        root["server"], {"name", "command", "args"}, field="server"
    )
    raw_args = server_value["args"]
    if (
        type(raw_args) is not list
        or not raw_args
        or not all(
            type(item) is str
            and item
            and not any(character in item for character in "\x00\r\n")
            for item in raw_args
        )
    ):
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt field: server.args"
        )
    server = MCPServerSpec(
        _strict_string(server_value["name"], field="server.name"),
        _strict_string(server_value["command"], field="server.command"),
        tuple(raw_args),
    )
    if not os.path.isabs(server.command):
        raise CodeGraphOnboardingError(
            "CodeGraph integration receipt has a non-absolute MCP command"
        )
    if server.name != codegraph_server_name(repo):
        raise CodeGraphOnboardingError(
            "CodeGraph integration receipt has an unexpected server name"
        )
    suffix = ("mcp", str(repo), "--tool-surface", "full")
    prefix = server.args[: -len(suffix)]
    if server.args[-len(suffix) :] != suffix or prefix not in ((), ("-m", "codenib")):
        raise CodeGraphOnboardingError(
            "CodeGraph integration receipt has an unexpected MCP command"
        )

    clients_value = root["clients"]
    if type(clients_value) is not dict or not set(clients_value).issubset(
        CODEGRAPH_CLIENTS
    ):
        raise CodeGraphOnboardingError(
            "invalid CodeGraph integration receipt object: clients"
        )
    clients: list[ManagedClient] = []
    for name in CODEGRAPH_CLIENTS:
        if name not in clients_value:
            continue
        item = _strict_keys(
            clients_value[name], {"scope", "state"}, field=f"clients.{name}"
        )
        scope = _strict_string(item["scope"], field=f"clients.{name}.scope")
        state = _strict_string(item["state"], field=f"clients.{name}.state")
        if scope != _CLIENT_SCOPES[name] or state not in _CLIENT_STATES:
            raise CodeGraphOnboardingError(
                f"invalid CodeGraph integration receipt client: {name}"
            )
        clients.append(ManagedClient(name, scope, state))
    planner = (
        _parse_context_planner_receipt(root["context_planner"])
        if schema_version >= 2
        else None
    )
    return CodeGraphReceipt(repo, server, tuple(clients), planner)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context_planner_assets() -> tuple[dict[Path, str], str]:
    package = importlib.resources.files("codenib.agent.instructions.context_planner")
    resource_paths = {
        CONTEXT_PLANNER_ASSET_PATHS[0]: package.joinpath(
            "references", "mcp-routing.md"
        ),
        CONTEXT_PLANNER_ASSET_PATHS[1]: package.joinpath(
            "references", "packet-contract.md"
        ),
        CONTEXT_PLANNER_ASSET_PATHS[2]: package.joinpath(
            "references", "failure-modes.md"
        ),
        CONTEXT_PLANNER_ASSET_PATHS[3]: package.joinpath(
            "agents", "scope-search.md"
        ),
        CONTEXT_PLANNER_ASSET_PATHS[4]: package.joinpath(
            "agents", "impact-navigator.md"
        ),
        CONTEXT_PLANNER_ASSET_PATHS[5]: package.joinpath(
            "agents", "evidence-auditor.md"
        ),
    }
    try:
        contents = {
            path: resource.read_text(encoding="utf-8")
            for path, resource in resource_paths.items()
        }
        return (
            contents,
            package.joinpath("CLAUDE.md.fragment").read_text(encoding="utf-8"),
        )
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise CodeGraphOnboardingError(
            f"context-planner instruction assets are unavailable: {exc}"
        ) from exc


def _context_planner_block(fragment: str) -> str:
    return (
        f"{CONTEXT_PLANNER_MARKER_START}\n"
        f"{fragment.rstrip()}\n"
        f"{CONTEXT_PLANNER_MARKER_END}\n"
    )


def _safe_existing_file(path: Path, *, label: str) -> str | None:
    if path.is_symlink():
        raise CodeGraphOnboardingError(
            f"refusing to manage {label}: path is not a regular file: {path}"
        )
    if not path.exists():
        return None
    if not path.is_file():
        raise CodeGraphOnboardingError(
            f"refusing to manage {label}: path is not a regular file: {path}"
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CodeGraphOnboardingError(f"cannot read {label} {path}: {exc}") from exc


def _safe_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise CodeGraphOnboardingError(
            f"refusing to manage {label}: path is not a directory: {path}"
        )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _extract_context_planner_block(content: str) -> tuple[str, str, str] | None:
    starts = content.count(CONTEXT_PLANNER_MARKER_START)
    ends = content.count(CONTEXT_PLANNER_MARKER_END)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise CodeGraphOnboardingError(
            "context-planner markers are duplicated or unbalanced in .claude/CLAUDE.md"
        )
    start = content.index(CONTEXT_PLANNER_MARKER_START)
    end_marker = content.index(CONTEXT_PLANNER_MARKER_END)
    if end_marker < start:
        raise CodeGraphOnboardingError(
            "context-planner markers are out of order in .claude/CLAUDE.md"
        )
    end = end_marker + len(CONTEXT_PLANNER_MARKER_END)
    if content.startswith("\n", end):
        end += 1
    return content[:start], content[start:end], content[end:]


def _append_context_planner_block(content: str, block: str) -> str:
    if not content:
        return block
    return content.rstrip("\n") + "\n\n" + block


def _replace_context_planner_block(content: str, block: str) -> str:
    extracted = _extract_context_planner_block(content)
    if extracted is None:
        return _append_context_planner_block(content, block)
    prefix, _old_block, suffix = extracted
    return prefix + block + suffix


def _remove_context_planner_block(content: str) -> str:
    extracted = _extract_context_planner_block(content)
    if extracted is None:
        return content
    prefix, _block, suffix = extracted
    if prefix.strip() and suffix.strip():
        return prefix.rstrip("\n") + "\n\n" + suffix.lstrip("\n")
    return (prefix + suffix).strip("\n") + ("\n" if prefix or suffix else "")


def _context_planner_plan(
    repository: str | Path,
    receipt: CodeGraphReceipt | None = None,
) -> tuple[
    ContextPlannerInstallPlan,
    str,
    str,
    str,
    dict[Path, str],
]:
    repo = Path(repository).expanduser().resolve()
    skill_path = repo / CONTEXT_PLANNER_SKILL_PATH
    claude_path = repo / CONTEXT_PLANNER_CLAUDE_PATH
    asset_contents, fragment = _context_planner_assets()
    package = importlib.resources.files("codenib.agent.instructions.context_planner")
    try:
        skill_content = package.joinpath("SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise CodeGraphOnboardingError(
            f"context-planner instruction assets are unavailable: {exc}"
        ) from exc
    block = _context_planner_block(fragment)
    _safe_directory(repo / ".claude", label=".claude")
    _safe_directory(repo / ".claude" / "skills", label=".claude/skills")
    _safe_directory(repo / ".claude" / "agents", label=".claude/agents")
    _safe_directory(
        repo / ".claude" / "skills" / "context-planner",
        label=".claude/skills/context-planner",
    )
    _safe_directory(
        repo / ".claude" / "skills" / "context-planner" / "references",
        label="context-planner references",
    )
    current_skill = _safe_existing_file(skill_path, label="context-planner Skill")
    current_claude = _safe_existing_file(claude_path, label="CLAUDE.md")
    previous = receipt.context_planner if receipt is not None else None

    skill_created = current_skill is None
    if current_skill is None:
        skill_action = "create"
    elif current_skill == skill_content:
        skill_action = "current"
        skill_created = previous.skill_created if previous is not None else False
    elif previous is not None and _sha256_text(current_skill) == previous.skill_sha256:
        skill_action = "refresh"
        skill_created = previous.skill_created
    else:
        raise CodeGraphOnboardingError(
            f"refusing to overwrite unmanaged or modified context-planner Skill: {skill_path}"
        )

    claude_created = current_claude is None
    if current_claude is None:
        claude_action = "create"
    else:
        extracted = _extract_context_planner_block(current_claude)
        if extracted is None:
            claude_action = "append"
        else:
            _prefix, current_block, _suffix = extracted
            if current_block == block:
                claude_action = "current"
                claude_created = (
                    previous.claude_created if previous is not None else False
                )
            elif (
                previous is not None
                and _sha256_text(current_block) == previous.claude_block_sha256
            ):
                claude_action = "refresh"
                claude_created = previous.claude_created
            else:
                raise CodeGraphOnboardingError(
                    "refusing to overwrite an unmanaged or modified context-planner "
                    f"section in {claude_path}"
                )

    asset_actions: list[tuple[str, str]] = []
    managed_files: list[ManagedPlannerFile] = []
    previous_files = {
        item.path: item for item in (previous.managed_files if previous else ())
    }
    for relative_path, content in asset_contents.items():
        path = repo / relative_path
        current = _safe_existing_file(path, label=f"context-planner asset {relative_path}")
        prior = previous_files.get(relative_path.as_posix())
        if current is None:
            action = "create"
            created = prior.created if prior is not None else True
        elif current == content:
            action = "current"
            created = prior.created if prior is not None else False
        elif prior is not None and _sha256_text(current) == prior.sha256:
            action = "refresh"
            created = prior.created
        else:
            raise CodeGraphOnboardingError(
                "refusing to overwrite unmanaged or modified context-planner "
                f"asset: {path}"
            )
        asset_actions.append((relative_path.as_posix(), action))
        managed_files.append(
            ManagedPlannerFile(
                path=relative_path.as_posix(),
                sha256=_sha256_text(content),
                created=created,
            )
        )

    installation = ContextPlannerInstallation(
        skill_sha256=_sha256_text(skill_content),
        claude_block_sha256=_sha256_text(block),
        skill_created=skill_created,
        claude_created=claude_created,
        managed_files=tuple(managed_files),
    )
    return (
        ContextPlannerInstallPlan(
            installation,
            skill_action,
            claude_action,
            tuple(asset_actions),
        ),
        skill_content,
        block,
        current_claude or "",
        asset_contents,
    )


def install_context_planner(
    repository: str | Path,
    *,
    receipt: CodeGraphReceipt | None = None,
    dry_run: bool = False,
) -> ContextPlannerInstallPlan:
    """Install the project-local context-planner Skill and guidance."""

    plan, skill_content, block, current_claude, asset_contents = _context_planner_plan(
        repository,
        receipt,
    )
    if dry_run:
        return plan

    repo = Path(repository).expanduser().resolve()
    skill_path = repo / CONTEXT_PLANNER_SKILL_PATH
    claude_path = repo / CONTEXT_PLANNER_CLAUDE_PATH
    if plan.skill_action != "current":
        _atomic_write_text(skill_path, skill_content)
    for relative_path, action in plan.asset_actions:
        if action != "current":
            _atomic_write_text(
                repo / relative_path,
                asset_contents[Path(relative_path)],
            )
    if plan.claude_action == "create":
        _atomic_write_text(claude_path, block)
    elif plan.claude_action in {"append", "refresh"}:
        _atomic_write_text(
            claude_path,
            _replace_context_planner_block(current_claude, block),
        )
    return plan


def inspect_context_planner(
    repository: str | Path,
    receipt: CodeGraphReceipt | None = None,
) -> ContextPlannerInspection:
    """Report whether project-local planner files are current or drifted."""

    repo = Path(repository).expanduser().resolve()
    skill_path = repo / CONTEXT_PLANNER_SKILL_PATH
    claude_path = repo / CONTEXT_PLANNER_CLAUDE_PATH
    try:
        asset_contents, fragment = _context_planner_assets()
        package = importlib.resources.files(
            "codenib.agent.instructions.context_planner"
        )
        skill_content = package.joinpath("SKILL.md").read_text(encoding="utf-8")
        expected_block = _context_planner_block(fragment)
        skill = _safe_existing_file(skill_path, label="context-planner Skill")
        claude = _safe_existing_file(claude_path, label="CLAUDE.md")
        if skill is None:
            skill_state = "missing"
        elif skill == skill_content:
            skill_state = "current"
        else:
            skill_state = "drifted"
        if claude is None:
            claude_state = "missing"
        else:
            extracted = _extract_context_planner_block(claude)
            claude_state = (
                "missing"
                if extracted is None
                else "current" if extracted[1] == expected_block else "drifted"
            )
        asset_states = []
        for relative_path, expected in asset_contents.items():
            observed = _safe_existing_file(
                repo / relative_path,
                label=f"context-planner asset {relative_path}",
            )
            asset_states.append(
                (
                    relative_path.as_posix(),
                    "missing"
                    if observed is None
                    else "current"
                    if observed == expected
                    else "drifted",
                )
            )
    except (CodeGraphOnboardingError, OSError, UnicodeError) as exc:
        return ContextPlannerInspection(
            "drifted", "drifted", "drifted", str(exc)
        )

    states = (skill_state, claude_state, *(state for _path, state in asset_states))
    if "drifted" in states:
        state = "drifted"
    elif "missing" in states:
        state = "missing"
    else:
        state = "current"
    managed = receipt is not None and receipt.context_planner is not None
    detail = (
        f"Skill {skill_state}; assets "
        f"{sum(state == 'current' for _path, state in asset_states)}/"
        f"{len(asset_states)} current; CLAUDE.md {claude_state}"
    )
    if state == "current" and not managed:
        detail += "; installation is not recorded in the CodeNib receipt"
    return ContextPlannerInspection(
        state,
        skill_state,
        claude_state,
        detail,
        tuple(asset_states),
    )


def remove_context_planner(
    repository: str | Path,
    installation: ContextPlannerInstallation,
) -> tuple[str, ...]:
    """Remove unchanged files or blocks recorded by CodeNib."""

    repo = Path(repository).expanduser().resolve()
    skill_path = repo / CONTEXT_PLANNER_SKILL_PATH
    claude_path = repo / CONTEXT_PLANNER_CLAUDE_PATH
    removed: list[str] = []
    skill = _safe_existing_file(skill_path, label="context-planner Skill")
    if skill is not None:
        if _sha256_text(skill) != installation.skill_sha256:
            raise CodeGraphOnboardingError(
                f"refusing to remove modified context-planner Skill: {skill_path}"
            )
        if installation.skill_created:
            skill_path.unlink()
            removed.append(str(CONTEXT_PLANNER_SKILL_PATH))

    for managed_file in installation.managed_files:
        path = repo / managed_file.path
        observed = _safe_existing_file(
            path,
            label=f"context-planner asset {managed_file.path}",
        )
        if observed is None:
            continue
        if _sha256_text(observed) != managed_file.sha256:
            raise CodeGraphOnboardingError(
                f"refusing to remove modified context-planner asset: {path}"
            )
        if managed_file.created:
            path.unlink()
            removed.append(managed_file.path)

    claude = _safe_existing_file(claude_path, label="CLAUDE.md")
    if claude is not None:
        extracted = _extract_context_planner_block(claude)
        if extracted is not None:
            if _sha256_text(extracted[1]) != installation.claude_block_sha256:
                raise CodeGraphOnboardingError(
                    "refusing to remove a modified context-planner section from "
                    f"{claude_path}"
                )
            if (
                installation.claude_created
                and extracted[0].strip() == ""
                and extracted[2].strip() == ""
            ):
                claude_path.unlink()
            else:
                _atomic_write_text(claude_path, _remove_context_planner_block(claude))
            removed.append(str(CONTEXT_PLANNER_CLAUDE_PATH))
    return tuple(removed)


def write_codegraph_receipt(receipt: CodeGraphReceipt) -> Path:
    """Atomically write a private, deterministic integration receipt."""

    path = codegraph_receipt_path(receipt.repository)
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


def remove_codegraph_receipt(receipt: CodeGraphReceipt) -> None:
    """Remove the receipt after every managed client has been reconciled."""

    codegraph_receipt_path(receipt.repository).unlink(missing_ok=True)


def _invoke_client(
    command: Sequence[str],
    *,
    repository: Path,
    runner: RunCommand,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            tuple(command),
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodeGraphOnboardingError(
            f"command failed to start: {command[0]}: {exc}"
        ) from exc


def inspect_server_command(
    server: MCPServerSpec,
    repository: str | Path,
    *,
    runner: RunCommand = subprocess.run,
) -> ServerCommandInspection:
    """Verify that the recorded executable resolves this CodeNib runtime."""

    if not os.path.isabs(server.command):
        raise CodeGraphOnboardingError("managed MCP server command is not absolute")
    mcp_suffix = (
        "mcp",
        str(Path(repository).expanduser().resolve()),
        "--tool-surface",
        "full",
    )
    if server.args[-len(mcp_suffix) :] != mcp_suffix:
        raise CodeGraphOnboardingError("managed MCP server command is malformed")
    prefix = server.args[: -len(mcp_suffix)]
    result = _invoke_client(
        (server.command, *prefix, "--version"),
        repository=Path(repository).expanduser().resolve(),
        runner=runner,
    )
    raw_output = result.stdout or result.stderr or ""
    observed = " ".join((raw_output or "no output").split())
    expected = f"codenib {package_version()}"
    version_ready = result.returncode == 0 and expected in {
        line.strip() for line in raw_output.splitlines()
    }
    if not version_ready:
        return ServerCommandInspection(
            False,
            f"expected {expected!r}, observed {observed[:240]!r}",
        )
    probe = _invoke_client(
        (server.command, *prefix, "mcp", "--runtime-probe"),
        repository=Path(repository).expanduser().resolve(),
        runner=runner,
    )
    probe_output = probe.stdout or probe.stderr or ""
    probe_marker = "codenib codegraph mcp runtime ready"
    ready = probe.returncode == 0 and probe_marker in {
        line.strip() for line in probe_output.splitlines()
    }
    return ServerCommandInspection(
        ready,
        (
            expected
            if ready
            else "CodeGraph MCP runtime probe failed: "
            + " ".join((probe_output or "no output").split())[:240]
        ),
    )


def _codex_matches(output: str, server: MCPServerSpec) -> bool:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return False
    if type(value) is not dict:
        return False
    transport = value.get("transport")
    return bool(
        value.get("name") == server.name
        and value.get("enabled") is True
        and type(transport) is dict
        and transport.get("type") == "stdio"
        and transport.get("command") == server.command
        and transport.get("args") == list(server.args)
        and transport.get("cwd") is None
        and transport.get("env") in (None, {})
    )


def _claude_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if not line.startswith("  ") or ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        if key and key not in fields:
            fields[key] = value.strip()
    return fields


def _claude_matches(output: str, server: MCPServerSpec) -> bool:
    fields = _claude_fields(output)
    return bool(
        fields.get("Scope", "").startswith("Local config")
        and fields.get("Type") == "stdio"
        and fields.get("Command") == server.command
        and fields.get("Args") == " ".join(server.args)
    )


def inspect_client_registration(
    client: str,
    server: MCPServerSpec,
    repository: str | Path,
    *,
    runner: RunCommand = subprocess.run,
    which: FindCommand = shutil.which,
) -> ClientInspection:
    """Inspect one registration using the owning client's public CLI."""

    if client not in CODEGRAPH_CLIENTS:
        raise CodeGraphOnboardingError(f"unsupported agent client: {client}")
    executable = which(client)
    if executable is None:
        return ClientInspection(client, False, False, False, "command not found")
    command = (
        (executable, "mcp", "get", "--json", server.name)
        if client == "codex"
        else (executable, "mcp", "get", server.name)
    )
    result = _invoke_client(
        command,
        repository=Path(repository).expanduser().resolve(),
        runner=runner,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "not configured").split())
        missing_markers = ("no mcp server named", "not found")
        if not any(marker in detail.lower() for marker in missing_markers):
            raise CodeGraphOnboardingError(
                f"cannot inspect {client} MCP configuration: {detail[:400]}"
            )
        return ClientInspection(client, True, False, False, detail[:240])
    matches = (
        _codex_matches(result.stdout, server)
        if client == "codex"
        else _claude_matches(result.stdout, server)
    )
    return ClientInspection(
        client,
        True,
        True,
        matches,
        "configuration matches" if matches else "configuration differs",
    )


def add_client_registration(
    client: str,
    server: MCPServerSpec,
    repository: str | Path,
    *,
    runner: RunCommand = subprocess.run,
    which: FindCommand = shutil.which,
) -> None:
    """Ask a native client to add the exact stdio registration."""

    executable = which(client)
    if executable is None:
        raise CodeGraphOnboardingError(f"agent client command not found: {client}")
    if client == "codex":
        command = (executable, "mcp", "add", server.name, "--", *server.argv)
    elif client == "claude":
        command = (
            executable,
            "mcp",
            "add",
            "--scope",
            "local",
            server.name,
            "--",
            *server.argv,
        )
    else:
        raise CodeGraphOnboardingError(f"unsupported agent client: {client}")
    result = _invoke_client(
        command,
        repository=Path(repository).expanduser().resolve(),
        runner=runner,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "unknown error").split())
        raise CodeGraphOnboardingError(
            f"{client} rejected the MCP registration: {detail[:400]}"
        )


def remove_client_registration(
    client: str,
    server: MCPServerSpec,
    repository: str | Path,
    *,
    runner: RunCommand = subprocess.run,
    which: FindCommand = shutil.which,
) -> None:
    """Ask a native client to remove one previously verified registration."""

    executable = which(client)
    if executable is None:
        raise CodeGraphOnboardingError(f"agent client command not found: {client}")
    command = (
        (executable, "mcp", "remove", server.name)
        if client == "codex"
        else (executable, "mcp", "remove", server.name, "--scope", "local")
    )
    result = _invoke_client(
        command,
        repository=Path(repository).expanduser().resolve(),
        runner=runner,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout or "unknown error").split())
        raise CodeGraphOnboardingError(f"{client} rejected MCP removal: {detail[:400]}")


__all__ = [
    "CODEGRAPH_CLIENTS",
    "CONTEXT_PLANNER_ASSET_PATHS",
    "CONTEXT_PLANNER_CLAUDE_PATH",
    "CONTEXT_PLANNER_MANAGED_PATHS",
    "CONTEXT_PLANNER_MARKER_END",
    "CONTEXT_PLANNER_MARKER_START",
    "CONTEXT_PLANNER_SKILL_PATH",
    "ClientInspection",
    "CodeGraphOnboardingError",
    "CodeGraphReceipt",
    "ContextPlannerInstallation",
    "ContextPlannerInspection",
    "ContextPlannerInstallPlan",
    "ManagedPlannerFile",
    "MCPServerSpec",
    "ServerCommandInspection",
    "add_client_registration",
    "codegraph_receipt_path",
    "codegraph_server_name",
    "install_context_planner",
    "inspect_client_registration",
    "inspect_context_planner",
    "inspect_server_command",
    "load_codegraph_receipt",
    "make_server_spec",
    "remove_client_registration",
    "remove_context_planner",
    "remove_codegraph_receipt",
    "resolve_codenib_command",
    "resolve_requested_clients",
    "write_codegraph_receipt",
]

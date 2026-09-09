# SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from codenib.workspace import scan_workspace


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _node_monorepo(root: Path) -> None:
    _write(
        root / "package.json",
        json.dumps(
            {
                "name": "root",
                "private": True,
                "workspaces": ["packages/*"],
                "dependencies": {"leftpad": "^1.0.0"},
            }
        ),
    )
    _write(
        root / "packages" / "foo" / "package.json",
        json.dumps({"name": "@co/foo", "version": "1.0.0"}),
    )
    _write(
        root / "packages" / "bar" / "package.json",
        json.dumps(
            {
                "name": "@co/bar",
                "version": "1.0.0",
                "dependencies": {"@co/foo": "workspace:*"},
            }
        ),
    )
    _write(root / "packages" / "bar" / "index.js", "module.exports = 1;\n")


def _by_path(model):
    return {project.project_path: project for project in model.projects}


def test_scan_node_workspace_resolves_internal_dependency(tmp_path):
    _node_monorepo(tmp_path)
    model = scan_workspace(str(tmp_path))

    projects = _by_path(model)
    assert set(projects) >= {".", "packages/foo", "packages/bar"}
    bar = projects["packages/bar"]
    resolved = [dep for dep in bar.dependencies if dep.target_project_id is not None]
    assert [(dep.declared_name, dep.target_project_id) for dep in resolved] == [
        ("@co/foo", "project://packages/foo")
    ]
    assert model.topology_digest.startswith("sha256:")
    assert model.metadata_digest.startswith("sha256:")


def test_scan_ambiguous_package_name_stays_unresolved(tmp_path):
    _write(
        tmp_path / "package.json",
        json.dumps({"private": True, "workspaces": ["a/*", "b/*"]}),
    )
    _write(tmp_path / "a" / "dup" / "package.json", json.dumps({"name": "dup"}))
    _write(tmp_path / "b" / "dup" / "package.json", json.dumps({"name": "dup"}))
    _write(
        tmp_path / "a" / "user" / "package.json",
        json.dumps({"name": "user", "dependencies": {"dup": "^1.0.0"}}),
    )

    model = scan_workspace(str(tmp_path))
    user = _by_path(model)["a/user"]
    dup_deps = [dep for dep in user.dependencies if dep.declared_name == "dup"]
    assert len(dup_deps) == 1
    assert dup_deps[0].target_project_id is None
    assert dup_deps[0].resolution == "unresolved"


def test_scan_cargo_path_dependency(tmp_path):
    _write(
        tmp_path / "Cargo.toml",
        '[workspace]\nmembers = ["crates/*"]\n',
    )
    _write(tmp_path / "crates" / "base" / "Cargo.toml", '[package]\nname = "base"\n')
    _write(
        tmp_path / "crates" / "app" / "Cargo.toml",
        '[package]\nname = "app"\n[dependencies]\nbase = { path = "../base" }\n',
    )

    model = scan_workspace(str(tmp_path))
    app = _by_path(model)["crates/app"]
    assert [(dep.declared_name, dep.target_project_id) for dep in app.dependencies] == [
        ("base", "project://crates/base")
    ]


def test_scan_python_dependency(tmp_path):
    _write(
        tmp_path / "libs" / "core" / "pyproject.toml", '[project]\nname = "co-core"\n'
    )
    _write(
        tmp_path / "apps" / "web" / "pyproject.toml",
        '[project]\nname = "co-web"\ndependencies = ["co-core>=1"]\n',
    )

    model = scan_workspace(str(tmp_path))
    web = _by_path(model)["apps/web"]
    assert [(dep.declared_name, dep.target_project_id) for dep in web.dependencies] == [
        ("co-core", "project://libs/core")
    ]


def test_scan_without_manifests_yields_synthetic_root(tmp_path):
    _write(tmp_path / "main.py", "print(1)\n")
    model = scan_workspace(str(tmp_path))
    assert [project.project_id for project in model.projects] == ["project://."]
    assert model.projects[0].synthetic


def test_scan_adapters_ignore_foreign_manifests(tmp_path):
    # package.json "dependencies" must not emit Cargo-style records and
    # vice versa: every edge's source manifest belongs to its adapter kind.
    _node_monorepo(tmp_path)
    _write(
        tmp_path / "Cargo.toml",
        '[workspace]\nmembers = ["crates/*"]\n',
    )
    _write(tmp_path / "crates" / "base" / "Cargo.toml", '[package]\nname = "base"\n')
    model = scan_workspace(str(tmp_path))
    for project in model.projects:
        for dep in project.dependencies:
            if dep.source_manifest.endswith("package.json"):
                assert (
                    dep.unresolved_reason != "no internal Cargo path/workspace target"
                )
    bar = _by_path(model)["packages/bar"]
    assert len(bar.dependencies) == 1


def test_scan_is_deterministic(tmp_path):
    _node_monorepo(tmp_path)
    first = scan_workspace(str(tmp_path))
    second = scan_workspace(str(tmp_path))
    assert first.topology_digest == second.topology_digest
    assert first.metadata_digest == second.metadata_digest
    assert first.to_dict() == second.to_dict()

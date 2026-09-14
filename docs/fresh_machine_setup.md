<!--
SPDX-FileCopyrightText: 2025-2026 CodeNib Contributors

SPDX-License-Identifier: Apache-2.0
-->

# Fresh Machine Setup (Minimal Command Set)

Install CodeNib from this fork, install the SCIP providers for a monorepo,
init the required graph + MCP, and copy in the Claude skills. Required steps
come first, in order; optional extensions (Zoekt, semantic/full views) are at
the bottom.

Replace `/path/to/monorepo` with your monorepo checkout path. That checkout
must be a clean Git working tree. Requires Python 3.10+, Git, curl, and tar.

## 1. Clone this fork and install CodeNib natively

Install from this repo (`https://github.com/KushalRegmi61/CodeNib`), not from
PyPI, so local updates are included:

```bash
git clone https://github.com/KushalRegmi61/CodeNib.git
cd CodeNib
python -m pip install -e ".[graph,mcp]"
```

- `-e` installs editable from source. `graph` adds the graph runtime (igraph
  + protobuf), `mcp` the MCP SDK. This is the complete required install:
  graph + MCP only. The `semantic` (vector) and `zoekt` extras are installed
  only if you take those optional steps at the bottom.
- `codenib --version` should now resolve from this checkout.

## 2. Install the SCIP providers for the monorepo

A monorepo uses the SCIP graph route (per-language indexers:
`scip-python`, `scip-typescript`, `scip-go`, `rust-analyzer scip`,
`scip-java`, `scip-dotnet`, `scip-ruby`, `scip-php`; C/C++ uses clangd).
Install the pinned providers for the monorepo's detected languages:

```bash
codenib toolchain install /path/to/monorepo --scope graph
```

This installs under `~/.codenib/toolchains`. Confirm the route is complete:

```bash
codenib doctor /path/to/monorepo --require graph
```

This installs the static-graph (SCIP) providers only. Live LSP servers
(`--scope lsp`) are a separate surface used for loose-file fallback and
regression checks; this guide does not install them, and MCP serving does
not need them — the static graph answers LSP-shaped definition/reference
queries without launching a live server.

If language detection is ambiguous, pin it explicitly (repeat or comma-separate):

```bash
codenib toolchain install /path/to/monorepo --scope graph \
  --language python --language typescript --language go
codenib doctor /path/to/monorepo --require graph \
  --language python --language typescript --language go
```

OS and project prerequisites (`clangd`, JDKs, `compile_commands.json`) are
reported, not installed; add those via your OS package manager.

## 3. Init the required graph + MCP for your agent

With the repo path typed, run `init` for the agent you use. This builds the
required `bm25` + `symbol_graph` views (reusing the step 2 providers) and
registers the MCP server with that client:

```bash
codenib codegraph init /path/to/monorepo --agent claude
```

```bash
codenib codegraph init /path/to/monorepo --agent codex
```

Omit `--agent` to auto-detect and register both at once. At least one of the
two CLIs must be installed, otherwise `init` fails with "no supported agent
client was found".

## 4. Copy in the Claude skills

```bash
codenib codegraph init /path/to/monorepo --install-context-planner
```

Reuses everything from steps 2-3 (installing only what is missing) and
installs project-local content only (never user-level): the
`context-planner` Skill, its three read-only agents (`scope-search`,
`impact-navigator`, `evidence-auditor`) with routing and packet references
under `.claude/`, plus a managed section in `CLAUDE.md` (repo root). Re-running
reconciles unchanged CodeNib-owned content and refuses to overwrite manually
modified files.

## 5. Verify

```bash
codenib codegraph status /path/to/monorepo
```

Diagnoses index freshness, source identity, toolchain, and client
registrations. Remove only the managed client registrations (index stays
reusable):

```bash
codenib codegraph uninstall /path/to/monorepo
```

## Optional extensions

### 6. (Optional) Install Zoekt

Skip unless you want the `--preset full` option below; the graph and semantic
paths do not need Zoekt.

`codenib toolchain` does **not** install Zoekt. The view builder shells out to
`zoekt-git-index` and serving shells out to `zoekt-webserver`, both resolved
from `PATH`. Build the pinned module from this checkout (`go-tool`
bootstraps its own Go, so no Go prerequisite):

```bash
cd /path/to/CodeNib
export CODENIB_SCIP_TOOLS_DIR="$HOME/.codenib/scip-tools"
make zoekt-tool
export PATH="$CODENIB_SCIP_TOOLS_DIR/go-tools/bin:$PATH"
```

- Pinning `CODENIB_SCIP_TOOLS_DIR` matters: the Makefile default is
  `/tmp/codenib/scip-tools`, which does not survive a reboot.
- Keep both exports (or add them to your shell profile) for every later step.

Verify:

```bash
command -v zoekt-git-index zoekt-webserver
```

> Zoekt builds from the fixed commit tree and supports only the default source
> policy: requesting the `zoekt` view (including `--preset full`) fails closed
> when the indexed tree does not exactly match the authenticated checkout, a
> tracked path is excluded, or a custom exclusion set is non-empty.
> Authenticated MCP serving for Zoekt currently requires Linux `/proc`.

### 7. (Optional) Extend the views: semantic or full

Step 3 built `bm25` + `symbol_graph`. Current views are reused; only the
missing ones build. Pick at most one:

**Semantic** (adds `vector`). Install the vector dependencies first
(sentence-transformers + faiss):

```bash
cd /path/to/CodeNib
python -m pip install -e ".[semantic]"
codenib index /path/to/monorepo --preset semantic
```

**Full** (adds `vector` + `zoekt`). Install both extras, then build.
Requires the step 6 binaries on `PATH`:

```bash
cd /path/to/CodeNib
python -m pip install -e ".[semantic,zoekt]"
codenib index /path/to/monorepo --preset full
```

Preset → views (`codenib/cli.py`):

| preset | views |
| --- | --- |
| `fast` | `bm25` |
| `semantic` | `bm25`, `vector` |
| `graph` | `bm25`, `symbol_graph` |
| `full` | `bm25`, `vector`, `symbol_graph`, `zoekt` |

On a small GPU, add `--embedding-provider huggingface --embedding-batch-size 16`.

No re-registration is needed after extending the views: the step 3
registration points at `codenib mcp /path/to/monorepo`, which loads the
current manifest on every serve, so later-built views are picked up
automatically. Re-run `codenib codegraph status /path/to/monorepo` after
completing any optional step.

## Troubleshooting

- Node-based indexer out of memory:

  ```bash
  export NODE_OPTIONS="--max-old-space-size=16384"
  ```

- Clean comparison after a tool or graph-logic change: add `--rebuild` to
  `codenib index`.
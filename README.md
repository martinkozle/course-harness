# Course Harness

Course Harness is a local-first application for creating source-grounded Courses in a
human-readable Course Workspace.

## Install and launch

Course Harness requires Python 3.14. Run the published package without a permanent installation:

```bash
uvx --python 3.14 course-harness
```

Or install it as a command-line tool:

```bash
uv tool install --python 3.14 course-harness
course-harness
```

The package contains the production web interface. Course Authors do not need Node.js, Bun, Nix,
Docker, a database, or a paid model provider to launch the Workspace Launcher. LibreOffice is
optional and enables high-fidelity slide thumbnails when its `libreoffice` or `soffice` executable
is available on `PATH`; semantic previews remain available without it.

To try an unreleased revision directly from GitHub:

```bash
uvx --python 3.14 --from git+https://github.com/martinkozle/course-harness.git course-harness
```

The command starts one loopback-only process, opens `http://127.0.0.1:8765`, and shows the
Workspace Launcher. Use the operating-system folder chooser to create or open a Course Workspace.
Pass an explicit path (including `.`) to bypass the launcher, `--no-browser` to avoid opening the
application automatically, or `--port <number>` to choose another local port:

```bash
course-harness /path/to/course-workspace
```

Course Harness stores canonical Course state in readable Workspace files. Runtime caches,
application state, and provider credentials stay in platform-native user locations outside Course
Workspaces. Provider credentials use a safe operating-system keyring when one is available and a
private application file otherwise; the in-app runtime diagnostics show the active storage mode,
exact runtime locations, and available capabilities.

Each Workspace can keep multiple Conversations in that private application state. Use
**Conversations** in the Course Agent panel to start or reopen one, rename it, archive it, or delete
it. Starting a new Conversation reuses an existing empty draft, so blank Conversations do not
accumulate. **Compact current conversation** shows an editable summary for review before using it as the
earlier context for future model runs. The full transcript remains visible. Resolve any pending
agent approval before compacting, archiving, or deleting its Conversation. An existing single
transcript is migrated automatically when that Workspace's Conversations are first opened.

## Development requirements

- [Nix](https://nixos.org/) with flakes enabled

No Docker daemon, database server, or external service is needed for the initial Workspace
experience.

## Enter the development environment

```bash
nix develop
```

The checked-in flake pins Python, uv, Bun, Node.js, Chromium, and Git. Python packages remain pinned
by `uv.lock`, while frontend packages remain pinned by `bun.lock`.

If you use direnv with nix-direnv, install and hook direnv into your login shell first, then allow
the checked-in `.envrc` once:

```bash
direnv allow
```

After that, entering the repository activates the flake automatically. Restart terminal tools such
as Codex after activation so they inherit the development environment:

```bash
codex
```

Without direnv, launch Codex directly through the shell with `nix develop -c codex`.

Entering the development shell also installs the repository's generated pre-commit and pre-push
Git hooks. They cover Biome, Ruff linting and formatting, ty, nixfmt, uv lock freshness, syntax
checks, and common repository hygiene checks. Vendored agent skills and generated frontend bundles
are excluded. Run the same hooks across the full repository with:

```bash
pre-commit run --all-files
```

The hook configuration lives in `flake.nix`; `.pre-commit-config.yaml` is generated and ignored.

## Launch from a development checkout

```bash
uv sync --locked
bun install --frozen-lockfile
bun run build
uv run course-harness
```

The last command launches the checkout. Pass a Workspace path to bypass the launcher:

```bash
uv run course-harness /path/to/course-workspace
```

Course Harness rejects missing paths, files, symbolic links, and the filesystem root as unsafe
Workspace choices. It does not expose a general filesystem API.

## Development

For frontend hot reload, start the API and Vite in separate terminals:

```bash
uv run course-harness /path/to/course-workspace --no-browser
bun run dev
```

Vite serves the development UI at `http://127.0.0.1:5173` and proxies `/api` to Course Harness.

Run focused and full checks with:

```bash
pre-commit run --all-files
nix flake check
uv run pytest tests/test_app.py
uv run pytest tests/test_cli.py
uv run ruff check .
uv run ruff format --check .
uv run ty check
bun run lint
bun run typecheck
bun run build
bun run test:e2e
uv run pytest
```

Build and smoke-test the wheel in an isolated temporary environment with:

```bash
uv run python scripts/verify_distribution.py
```

This verifies the wheel metadata and bundled React assets, launches the wheel through `uvx`,
installs and launches that same wheel with `uv tool install` in a temporary isolated tool home,
and launches the current committed revision through a direct `uvx` `git+file://` URL. Each launch
checks both the health endpoint and production interface. Release publication is a separate
explicit step; building and verifying locally never uploads an artifact or changes your real uv
tool installation.

`bun run test:e2e` builds the production frontend, launches Course Harness on a verified-empty
smoke Workspace under the ignored local cache, and runs the Playwright journey. Inside Nix, the test
uses the flake-pinned Chromium. Outside Nix, install the Playwright browser once with
`bunx playwright install --with-deps chromium`, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a
compatible Chromium executable.

## Without Nix

Nix is the supported development environment, but the application remains usable without it.
Install Python 3.14, uv, Bun 1.3 or newer, Node.js, and Chromium, then follow the same uv and Bun
commands above. No Nix store paths are recorded in `package.json` or application configuration.

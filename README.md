# Course Harness

Course Harness is a local-first application for creating source-grounded Courses in a
human-readable Course Workspace.

## Requirements

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

## Install and launch Course Harness

```bash
uv sync --locked
bun install --frozen-lockfile
bun run build
uv run course-harness
```

The last command starts one loopback-only process, opens `http://127.0.0.1:8765`, and shows the
Workspace Launcher. Use the operating-system folder chooser to create or open a Course Workspace.
Pass an explicit path (including `.`) to bypass the launcher, `--no-browser` to avoid opening the
application automatically, or `--port <number>` to choose another local port:

```bash
uv run course-harness /path/to/course-workspace
```

Course Harness stores the Course Plan in readable `course.yaml`. Runtime caches are not required to
reopen the Course, and recent-Workspace metadata lives in platform-native user data outside Course
folders.

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

`bun run test:e2e` builds the production frontend, launches Course Harness on a verified-empty
smoke Workspace under the ignored local cache, and runs the Playwright journey. Inside Nix, the test
uses the flake-pinned Chromium. Outside Nix, install the Playwright browser once with
`bunx playwright install --with-deps chromium`, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a
compatible Chromium executable.

## Without Nix

Nix is the supported development environment, but the application remains usable without it.
Install Python 3.14, uv, Bun 1.3 or newer, Node.js, and Chromium, then follow the same uv and Bun
commands above. No Nix store paths are recorded in `package.json` or application configuration.

# Course Harness

Course Harness is a local-first application for creating source-grounded Courses in a
human-readable Course Workspace.

## Requirements

- [uv](https://docs.astral.sh/uv/) with Python 3.14 support
- [Bun](https://bun.sh/) 1.3 or newer

No Docker daemon, database server, or external service is needed for the initial Workspace
experience.

## Install and launch

```bash
uv sync --locked
bun install --frozen-lockfile
bun run build
uv run course-harness /path/to/course-workspace
```

The last command starts one loopback-only process, opens `http://127.0.0.1:8765`, and binds the
application to the existing directory you selected. Pass `--no-browser` when the browser should
not open automatically, or `--port <number>` to choose another local port.

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
smoke Workspace under the ignored local cache, and runs the Playwright journey. Install its browser
once with
`bunx playwright install --with-deps chromium`. On machines with an existing Chromium build, set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to its executable path instead.

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

Run `course-harness --version` to check the installed build.

Course Harness stores canonical Course state in readable Workspace files. Runtime caches,
application state, and provider credentials stay in platform-native user locations outside Course
Workspaces. Provider credentials use a safe operating-system keyring when one is available and a
private application file otherwise; the in-app runtime diagnostics show the active storage mode,
exact runtime locations, and available capabilities.

For an OpenAI-compatible provider on another local machine, enter its API base URL (for example,
`http://blaze.home:8081/v1`) and select **Allow HTTP to another host**. HTTPS and loopback HTTP
work without this opt-in. Remote HTTP sends the API key and Course requests without encryption;
use it only on a network you trust.

After you open a Course, the workspace has three parts: a navigator on the left (Course Plan,
Sources, the numbered Lectures, Conversations, History, and Settings), the conversation with the
Course Agent, and a canvas that shows whatever you open. A new Course starts with your material:
drop papers, notes, or slides onto the start screen, discover papers, or reuse files from your
Library, then tell the agent what the Course should teach. You can also write the Course Plan
yourself.

**Sources** accepts PDF, `.pptx`, and `.docx` Resources. Files added from the start screen or the
**This course** tab are saved to your Library and included in the Course; the **Library** tab keeps
files without including them, and **Discover** searches public paper indexes (arXiv, Crossref,
Semantic Scholar, and OpenAlex) and code indexes. Web pages are accepted as well. Docling
extracts text, tables, and document structure for search and Source use, including text
recognition for scanned PDFs. On the first PDF upload, Course Harness asks before downloading
layout, table, and OCR models to the application cache outside the Course Workspace. Conversion
runs locally and stores a structured Docling result alongside the searchable Markdown. The
original files remain immutable Snapshots. **Settings → Diagnostics** also has a **Download PDF
models** control and **Rebuild search index**. If models are missing when you process or reprocess
an existing PDF, the same prompt appears. Cancel leaves the Resource unchanged. The download shows
progress across its layout, table, and OCR stages; stage progress is not a byte percentage.

The Course Agent can research too. Ask it to find sources for a topic and it searches the same
paper indexes. Its results appear under its reply; they are not Sources until they are added to
the course, and only added Sources can be cited. For an open-ended request the agent adds the most
relevant results itself and tells you which; for a narrow one it lists them and asks. Either way,
**Add to course** and **Remove from course** on each result let you decide. The agent can only add
an address that a search returned or that you gave it. A Course Plan must exist before Sources can
be added.

**Settings → Connectors** adds web search. A Connector is a remote MCP server whose tools the
Course Agent may use; **Exa** is set up by default and works without a key at Exa's free,
rate-limited tier. Put an Exa API key in its `x-api-key` header to raise the limits, or delete Exa
and add another search service with its own URL and headers. Header values and URLs marked secret
are stored like provider keys, in the operating-system keyring or a private file, never in a Course
Workspace. **Test** lists a Connector's tools so you can choose which the agent may use and which
one reads pages. Your requests, including search queries, are sent to each enabled Connector. When
a site blocks a direct download or renders its text with scripts, Course Harness reads the page
through that page reader instead and records which Connector read it. Only HTTPS Connectors, or
HTTP to this computer, are accepted.

Each Workspace can keep multiple Conversations in that private application state. The navigator
lists them; each row's menu renames, archives, or deletes one. A new Conversation stays an unsaved
draft until you send its first message, and an unsent message is kept per Conversation while you
switch. **Summarize earlier context** in the Conversation menu shows an editable summary for
review before using it as the earlier context for future model runs. The full transcript remains
visible. Resolve any pending agent approval before summarizing, archiving, or deleting its
Conversation. An existing single transcript is migrated automatically when that Workspace's
Conversations are first opened.

**History** lists changes since the last Course Revision, lets you save a named revision or restore
an earlier one, and holds published Course Releases. **Publish release** in the canvas bar prepares
a new Release; **Export PowerPoint** on a Lecture downloads a file from the current work without
publishing anything.

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

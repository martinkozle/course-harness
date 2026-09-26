# Course Harness

Course Harness is a local-first application for creating source-grounded Courses in a
human-readable Course Workspace.

## Install and launch

Install [uv](https://docs.astral.sh/uv/). It downloads Python 3.14 if you do not have it.

```bash
uvx --torch-backend cpu course-harness
```

Or install it as a command-line tool:

```bash
uv tool install --torch-backend cpu course-harness
course-harness
```

`--torch-backend cpu` installs the CPU build of PyTorch for local document conversion. Without it,
uv installs the CUDA build on Linux, which downloads several gigabytes of GPU libraries.

To run the latest revision from GitHub (no flag needed; the CPU build is selected automatically):

```bash
uvx --from git+https://github.com/martinkozle/course-harness.git course-harness
```

The command opens `http://127.0.0.1:8765` with the Workspace Launcher, where you create or open a
Course Workspace. Useful options:

```bash
course-harness /path/to/course-workspace  # open a Workspace directly (`.` works too)
course-harness --no-browser               # do not open the browser
course-harness --port 9000                # use another local port
```

Optional: install LibreOffice (`libreoffice` or `soffice` on `PATH`) for high-fidelity slide
thumbnails.

## What it does

- **Sources**: add PDF, `.pptx`, `.docx`, and web pages, or discover papers through arXiv,
  Crossref, Semantic Scholar, and OpenAlex. Documents are converted locally with Docling; the
  layout and OCR models are downloaded on first use, after you confirm.
- **Course Agent**: plans the Course, researches sources, and writes Lectures. Only Sources added
  to the Course can be cited.
- **Connectors**: web search and page reading through remote MCP servers. Exa is set up by default
  and works without a key.
- **History**: save and restore Course Revisions, publish Releases, and export Lectures to
  PowerPoint.

Canonical Course state lives in readable files in the Course Workspace. Caches, conversations, and
credentials live in platform-native user locations outside it; credentials go to the operating
system keyring when one is available. **Settings → Diagnostics** shows the exact locations.

To use an OpenAI-compatible provider on another machine over plain HTTP, select **Allow HTTP to
another host**. The API key and Course content are then sent unencrypted, so use it only on a
network you trust.

## Development

Development uses [Nix](https://nixos.org/) with flakes. The flake pins Python, uv, Bun, Node.js,
Chromium, and Git; `uv.lock` and `bun.lock` pin application packages.

```bash
nix develop   # or `direnv allow` once with nix-direnv
```

Entering the shell installs the pre-commit and pre-push hooks (Biome, Ruff, ty, nixfmt, uv lock
freshness, and hygiene checks). Their configuration lives in `flake.nix`.

Launch from a checkout:

```bash
uv sync --locked
bun install --frozen-lockfile
bun run build
uv run course-harness
```

For frontend hot reload, run the API and Vite side by side; Vite serves `http://127.0.0.1:5173`
and proxies `/api`:

```bash
uv run course-harness /path/to/course-workspace --no-browser
bun run dev
```

Checks:

```bash
pre-commit run --all-files
uv run pytest
bun run lint
bun run typecheck
bun run build
bun run test:e2e   # Playwright smoke journey
```

Outside Nix, install the Playwright browser once with `bunx playwright install --with-deps chromium`
or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`.

Build the wheel and smoke-test it through `uvx`, `uv tool install`, and a `git+file://` install of
the current commit, all in isolated temporary environments:

```bash
uv run python scripts/verify_distribution.py
```

Without Nix, install Python 3.14, uv, Bun 1.3 or newer, Node.js, and Chromium, then use the same
commands.

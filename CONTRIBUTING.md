# Contributing to Course Harness

## Setup

Development uses [Nix](https://nixos.org/) with flakes. The flake pins Python, uv, Bun, Node.js,
Chromium, and Git; `uv.lock` and `bun.lock` pin application packages.

```bash
nix develop   # or `direnv allow` once with nix-direnv
```

Entering the shell installs the pre-commit and pre-push hooks (Biome, Ruff, ty, nixfmt, uv lock
freshness, and hygiene checks). Their configuration lives in `flake.nix`.

Without Nix, install Python 3.14, uv, Bun 1.3 or newer, Node.js, and Chromium, then use the same
commands.

## Restricted networks and cloud agents

Some sandboxes, such as cloud coding-agent containers, allow `git clone` from GitHub but refuse
the archive downloads `nix develop` uses for flake inputs. `nix develop` then fails with
`unable to download 'https://github.com/.../archive/<rev>.tar.gz': HTTP error 403`. Use the
wrapper instead; it fetches the same locked revisions with git and leaves `flake.lock` unchanged:

```bash
scripts/nix-develop.sh                          # interactive shell
scripts/nix-develop.sh -c pre-commit run --all-files
scripts/nix-develop.sh -c uv run pytest
```

The first run builds the shell, including Chromium and LibreOffice, and can take several minutes;
later runs reuse the Nix store. Run checks inside the shell rather than with tools installed
elsewhere: the document conversion packages load native libraries, such as `libstdc++`, that the
shell provides, and the pre-commit configuration only exists inside it.

## Run from a checkout

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

## Checks

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

Build the wheel and smoke-test it through `uvx` and `uv tool install` in isolated temporary
environments. The script also checks that a `pinned` install holds exactly the versions in
`uv.lock`, which is what the `pinned` extra of a release installs:

```bash
uv run python scripts/verify_distribution.py
```

## Releases

The Release workflow runs the distribution check on every push to `main`. To publish a version:

```bash
uv version --bump minor   # or patch/major; updates pyproject.toml and uv.lock
git commit -am "Release 0.2.0" && git push
```

Then create a GitHub Release with a new tag `v0.2.0` on that commit (or push the tag with
`git tag v0.2.0 && git push origin v0.2.0`). The tag must match the package version. The workflow
publishes to PyPI through trusted publishing and attaches the wheel and source distribution to the
Release.

## Debugging Course Agent runs

Each Course Agent run is also traced for debugging, beside its conversation in the chat store:
`traces/<conversation id>.jsonl` holds one line per model request, response, and tool call, with
timings, token usage (including cached input tokens), and only the messages that changed since the
previous request. If a request sets `history_rewritten`, `instructions_changed`, or
`tools_changed`, the provider could not reuse its prompt cache for the conversation so far. A trace
is removed with its conversation, or after two weeks without new runs. The conversation itself is
saved around every model request, so stopping a run keeps everything up to that point.

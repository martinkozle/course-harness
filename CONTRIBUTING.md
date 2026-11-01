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

## Repository layout

The repository is a uv workspace (one root `uv.lock`) and a Bun workspace (one root `bun.lock`):

| Path | What it is |
| --- | --- |
| `packages/harness` | `course-harness`, the Python package published to PyPI |
| `packages/classroom` | `course-harness-classroom`, Course Harness Classroom; not published |
| `frontend/harness` | The Course Harness interface, built into `packages/harness/src/course_harness/static` |
| `frontend/classroom` | The Classroom interface, built into `frontend/classroom/dist` |
| `frontend/ui` | `@course-harness/ui`, code shared by both interfaces |
| `tests/e2e` | The Playwright smoke journey, including the Classroom Learner journey |

The root `pyproject.toml` holds the workspace, the development dependencies, and the Ruff, ty, and
pytest settings. `packages/harness` links `README.md`, `LICENSE`, and `uv.lock` from the root; a
small build hook puts the real files into the source distribution.

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

`bun run build` builds every interface. Course Harness Classroom runs with
`uv run course-classroom serve` (`--host`, `--port`); `bun run dev:classroom` serves its interface
with hot reload and proxies `/api`, `/l/` and `/i/` to port 8000.

### Course Harness Classroom

Unlike the Harness bundle, the Classroom bundle (`frontend/classroom/dist`) is not committed:
`bun run build` builds it for development and the Classroom Docker image builds its own. The
Python app serves it from `CLASSROOM_STATIC_DIR`, by default that folder of the checkout. The
Classroom reads its settings from the environment:

| Variable | Meaning | Default |
| --- | --- | --- |
| `CLASSROOM_DATA_DIR` | Mirrors, served Releases, rendered backgrounds and the SQLite store | `./classroom-data` |
| `CLASSROOM_ADMIN_TOKEN` | The Administrator token for `/admin`, at least 24 characters | unset: `/admin` is off |
| `CLASSROOM_PUBLIC_URL` | External base URL, used in Share Links; required when serving on a non-loopback host. Cookies are `Secure` unless it is `http://localhost` or `http://127.0.0.1` | the request URL |
| `CLASSROOM_TRUSTED_PROXIES` | Comma-separated reverse proxy addresses whose `X-Forwarded-*` headers are trusted (uvicorn `forwarded_allow_ips`) | unset: ignored |
| `CLASSROOM_FETCH_INTERVAL` | Seconds between background syncs; `0` turns them off | `900` |
| `CLASSROOM_FETCH_TIMEOUT` | Seconds one `git fetch` may take | `300` |
| `CLASSROOM_MAX_MIRROR_BYTES` | Largest a course's mirror may grow on disk | `1000000000` |
| `CLASSROOM_STATIC_DIR` | The built Classroom interface | `frontend/classroom/dist` |
| `CLASSROOM_ALLOW_LOCAL_REPOS` | `1` also accepts local paths, `file://`, `http://` and private hosts; development only | off |
| `CLASSROOM_RENDER_SANDBOX` | Docker image only: `required` never runs LibreOffice without its network-less sandbox | `auto` |

```bash
bun run build
CLASSROOM_ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
  CLASSROOM_PUBLIC_URL=http://127.0.0.1:8000 CLASSROOM_ALLOW_LOCAL_REPOS=1 \
  uv run course-classroom serve
```

Open `/admin`, paste the token, and attach a repository a Course Author pushed to. Behind a
TLS-terminating proxy, set `CLASSROOM_PUBLIC_URL` to the public `https://` address and
`CLASSROOM_TRUSTED_PROXIES` to the proxy's address, so per-client limits see real client
addresses.

The production image is `packages/classroom/Dockerfile` (build context: the repository root),
run by the root `compose.yaml`; [docs/deploy-classroom.md](docs/deploy-classroom.md) covers
building, the reverse proxy, the LibreOffice sandbox (`CLASSROOM_RENDER_SANDBOX`), backups and
upgrades. The image build fails unless only `course_harness/__init__.py` and
`course_harness/core/` of Course Harness ship (`packages/classroom/docker/check_image.py`).

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
uv version --package course-harness --bump minor   # or patch/major; updates its pyproject.toml and uv.lock
git commit -am "Release 0.2.0" && git push
```

Then create a GitHub Release with a new tag `v0.2.0` on that commit (or push the tag with
`git tag v0.2.0 && git push origin v0.2.0`). The tag must match the package version. The workflow
publishes to PyPI through trusted publishing and attaches the wheel and source distribution to the
Release.

## Course Workspace remotes

A Course Workspace's remote is the ordinary Git remote `origin`; **Push** in the Release canvas
runs the system `git` with your own configuration and credentials, with hooks disabled. From the
Course folder, it is close to:

```bash
git push origin main 'refs/tags/course-release/*' 'refs/course-harness/release-files/*'
```

Push first records missing Release files refs from local Release data, and leaves out (and
reports) a files ref that does not match its manifest and cannot be corrected locally.

`refs/course-harness/release-files/<slug>` names a commit that holds exactly a Release's
`inputs/template.pptx` and `artifacts/<id>.pptx` (ADR 0019). Publishing writes it as best effort.
A plain `git clone` does not fetch these refs; a reader such as the Classroom fetches three
refspecs:

```bash
git fetch <remote> '+refs/heads/main:refs/heads/main' \
  '+refs/tags/course-release/*:refs/tags/course-release/*' \
  '+refs/course-harness/release-files/*:refs/course-harness/release-files/*'
```

`course_harness.core.release_files` reads and verifies the files against the Release manifest:
Artifacts by exact size and SHA-256, the template by SHA-256, with only the size cap applied
before reading it, since the manifest records no template size.

## Debugging Course Agent runs

Each Course Agent run is also traced for debugging, beside its conversation in the chat store:
`traces/<conversation id>.jsonl` holds one line per model request, response, and tool call, with
timings, token usage (including cached input tokens), and only the messages that changed since the
previous request. If a request sets `history_rewritten`, `instructions_changed`, or
`tools_changed`, the provider could not reuse its prompt cache for the conversation so far. A trace
is removed with its conversation, or after two weeks without new runs. The conversation itself is
saved around every model request, so stopping a run keeps everything up to that point.

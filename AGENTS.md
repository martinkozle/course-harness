# Course Harness agent guidance

## Agent skills

### Issue tracker

Issues are tracked as uncommitted local Markdown under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context repository using root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.

## Architecture invariants

- A running application may be unbound only in the restricted Workspace Launcher; after selection,
  it is bound to at most one explicit Course Workspace.
- The Python package owns the HTTP boundary and serves the production React bundle.
- The browser may inspect the selected Workspace identity through `/api/workspace`; do not add a
  general filesystem browser or accept arbitrary paths through HTTP.
- Keep Course Author-facing copy neutral. Use “you” where possible and `Course Author` when the role
  must be named.
- Keep canonical Course state human-readable. Runtime caches and secrets never belong in a Course
  Workspace.

## Commands

See `README.md` for setup and launch instructions. Before handing off implementation work, run
all pre-commit hooks, Ruff, Ruff formatting, ty, the Python suite, frontend
lint/typechecking/build, and the Playwright smoke journey.

Run development commands inside the pinned Nix shell. When `IN_NIX_SHELL` is absent, prefix a
command with `nix develop -c`. Add system-facing tools to `flake.nix`; keep Python and frontend
application dependencies in `pyproject.toml`/`uv.lock` and `package.json`/`bun.lock` respectively.

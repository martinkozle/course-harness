# Course Harness agent guidance

## Agent skills

### Issue tracker

Issues are tracked as uncommitted local Markdown under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context repository using root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.

### Releases

Releases to PyPI and GitHub Releases follow `.agents/skills/release/SKILL.md`. GitHub Actions
publishes on a `v*` tag; never upload from a local machine.

## Architecture invariants

### Course Harness

- A running Course Harness may be unbound only in the restricted Workspace Launcher; after
  selection, it is bound to at most one explicit Course Workspace.
- The Course Harness Python package owns the HTTP boundary and serves the production React bundle.
- The Course Harness browser may inspect the selected Workspace identity through `/api/workspace`;
  do not add a general filesystem browser or accept arbitrary paths through HTTP.
- Keep Course Author-facing copy neutral. Use “you” where possible and `Course Author` when the role
  must be named.
- Keep canonical Course state human-readable. Runtime caches and secrets never belong in a Course
  Workspace.

### Classroom

- The Classroom serves only validated Course Release tags read from a bare mirror, never mutable
  working state, and never checks out or executes repository content.
- The Classroom never hosts Source binaries.
- The Administrator token never appears in a URL. Learner and Instructor Share Link tokens are
  exchanged for HttpOnly cookies and a redirect to a clean URL.
- Keep Learner- and Instructor-facing copy neutral. Use “you” where possible and name the role
  (`Learner`, `Instructor`, `Administrator`) only when it must be named.

## Commands

See `CONTRIBUTING.md` for development setup and launch instructions. Before handing off implementation work, run
all pre-commit hooks, Ruff, Ruff formatting, ty, the Python suite, frontend
lint/typechecking/build, and the Playwright smoke journey.

Run development commands inside the pinned Nix shell. When `IN_NIX_SHELL` is absent, prefix a
command with `nix develop -c`. If `nix develop` fails to download GitHub archives (HTTP 403, as in
cloud agent containers), use `scripts/nix-develop.sh -c` instead; it fetches the locked flake inputs
with git. Do not fall back to tools outside the shell. Add system-facing tools to `flake.nix`; keep
Python and frontend application dependencies in the owning workspace member
(`packages/*/pyproject.toml`, `frontend/*/package.json`) and the root `uv.lock`/`bun.lock`.

## Layout

Only `packages/harness` (`course-harness`) is published to PyPI; never upload `packages/classroom`.
The repository layout is in the table in `CONTRIBUTING.md`.

## Git handoff

Commits are the unit of completed work in this repository. After the required checks, commit the
files changed for the task before handing it back, and report the commit ID. Keep unrelated
pre-existing changes out of the commit. If a commit cannot be created, explain why and do not
describe the work as finished.

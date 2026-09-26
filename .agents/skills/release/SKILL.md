---
name: release
description: "Release a new version of Course Harness to PyPI and GitHub Releases. Use when asked to do a release, publish a version, cut 0.x.y, or bump and ship."
---

# Release Course Harness

Publishing is done by `.github/workflows/release.yml` when a `v*` tag is pushed. It verifies the
distribution, publishes to PyPI through trusted publishing (environment `pypi`), and attaches the
wheel and sdist to the GitHub Release. Never upload with `uv publish` and a token from this machine.

Use the `gh` CLI (already authenticated) for everything on GitHub. Run tools inside the Nix shell.

## Steps

1. **Start clean and current.** `git fetch`, working tree clean, local `main` not behind
   `origin/main`. If there are unpushed commits you did not make, ask before pushing them.

2. **Pick the version.** Use the version the user gave; otherwise `minor` for new behaviour,
   `patch` for fixes only. The project is pre-1.0; do not go to 1.0.0 unless asked.

3. **Bump and commit.**
   ```bash
   nix develop -c uv version <x.y.z>        # or --bump minor|patch; updates pyproject.toml and uv.lock
   git commit -m "Release <x.y.z>" -- pyproject.toml uv.lock
   git push origin main
   ```

4. **Wait for the `main` run to pass** before tagging (it takes about ten minutes):
   ```bash
   gh run list --workflow release.yml --branch main --limit 1
   gh run watch <id> --exit-status
   ```
   If it fails, read `gh run view <id> --log-failed`, fix, commit, push, and wait again.

5. **Write the release notes** from `git log v<previous>..HEAD` (read commit bodies, not just
   subjects). Follow the style of the previous Release (`gh release view v<previous>`):
   - An install/upgrade block: `uvx --torch-backend cpu course-harness@latest` and
     `uv tool upgrade course-harness`. The PyPI wheel needs `--torch-backend cpu`, or uv installs
     the multi-gigabyte CUDA build of PyTorch on Linux.
   - Changes grouped by area (for example Slides and export, Course Agent, Model providers,
     Interface, Installation), written for Course Authors: what changed for them, with fixes
     marked "Fixed:". Leave out purely internal refactors and CI plumbing.
   - End with `**Full changelog**: https://github.com/martinkozle/course-harness/compare/v<previous>...v<x.y.z>`.

   Write the notes to a temporary file outside the repository.

6. **Create the Release, which creates the tag and starts publishing.** `--target` needs the full
   SHA of the pushed commit that step 4 verified:
   ```bash
   gh release create v<x.y.z> --target "$(git rev-parse HEAD)" --title "<x.y.z>" --notes-file <notes>
   ```
   The tag must equal `v` + the `pyproject.toml` version, or the workflow stops.

7. **Watch the tag run** (`gh run list --workflow release.yml --limit 1`, then `gh run watch`).
   If only *Publish to PyPI* fails, fix the cause and run `gh run rerun <id> --failed`. Do not
   move the tag or bump again. An `invalid-publisher` error means PyPI's trusted publisher settings
   do not match repository `martinkozle/course-harness`, workflow `release.yml`, environment
   `pypi`. Only the user can change that on pypi.org.

8. **Verify, then report** the PyPI and Release URLs:
   ```bash
   curl -s https://pypi.org/pypi/course-harness/json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'
   gh release view v<x.y.z> --json assets --jq '.assets[].name'
   ```

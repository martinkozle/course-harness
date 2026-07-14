# Issue tracker: Local Markdown

Issues for this repository live as uncommitted Markdown files in `.scratch/`. The directory is intentionally ignored by Git: it is a local execution queue, while durable specifications, research, ADRs, and domain documentation remain tracked under `docs/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The local working copy of a spec may be `.scratch/<feature-slug>/spec.md`; the durable source spec may remain under `docs/specs/` and should be linked from the local copy.
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order.
- Triage state is recorded as a `Status:` line near the top of each issue file.
- Comments and local execution notes append under a `## Comments` heading.
- Do not commit `.scratch/` unless the repository deliberately switches to a shared tracked issue workflow later.

## When a skill says “publish to the issue tracker”

Create a file under `.scratch/<feature-slug>/`, creating the directory if necessary. Never combine multiple implementation tickets into one file.

## When a skill says “fetch the relevant ticket”

Read the referenced file from `.scratch/`. The user will normally provide its path, feature name, or issue number.

## Wayfinding operations

- **Map:** `.scratch/<effort>/map.md`
- **Child ticket:** `.scratch/<effort>/issues/NN-<slug>.md`, with `Type:` and `Status:` lines near the top.
- **Blocking:** a `Blocked by: NN, NN` line. A ticket is unblocked when all listed tickets are resolved.
- **Frontier:** open, unblocked, unclaimed tickets ordered by number.
- **Claim:** set `Status: claimed` before work.
- **Resolve:** append the answer under `## Answer`, set `Status: resolved`, and add a linked gist to the map’s Decisions-so-far.

## Implementation ticket operations

- Use tracer-bullet vertical slices that cross every necessary layer and are demonstrable independently.
- Store one ticket per file under `.scratch/<feature>/issues/`.
- Set `Status: ready-for-agent` only after acceptance criteria and blocking edges are complete.
- Work the frontier: a ticket is available only when all of its blockers are done.

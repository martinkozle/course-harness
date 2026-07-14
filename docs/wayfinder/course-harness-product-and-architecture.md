---
title: Course Harness product and architecture
label: wayfinder:map
status: open
---

# Course Harness product and architecture

## Destination

A decision-complete proof-of-concept specification for a local-first, agent-harness-first application that a Course Author can use to create a cited multi-lecture Course and export editable presentations through an arbitrary configured PowerPoint template.

## Notes

- Use `grill-with-docs`, `domain-modeling`, `research`, `codebase-design`, `frontend-design`, `vercel-react-best-practices`, and `tdd` where their ticket type calls for them.
- The Course Author-facing proof of concept and working software matter more than a separate academic report.
- Treat [IDEA.md](../../IDEA.md), [CONTEXT.md](../../CONTEXT.md), [the ADRs](../adr/), and [source-abstraction research](../research/source-abstraction-landscape.md) as standing context.
- Treat the [Course Harness proof-of-concept PRD](../specs/course-harness-proof-of-concept.md) as the consolidated `ready-for-agent` product contract.
- Inspect `../course-embroider` when comparing functionality, but Course Harness is conversational and workspace-oriented rather than a fixed batch FSM.
- Fixed stack: Python 3.14, uv, FastAPI, Pydantic AI, AG-UI, React, Bun, Ruff, and ty. Docker is optional packaging, not the primary local experience.
- Planning is the default. Tickets decide or de-risk; they do not build the finished product unless their question explicitly requires a disposable prototype.

## Decisions so far

- [Sharpen the multi-lecture authoring model](tickets/WF-003-sharpen-the-course-authoring-model.md) — Use a chat-first, progressively authored flat Course Plan with stable identities, flexible shared Sources, guided-or-autonomous runs, recoverable partial Current State, and conservative Release validation.

## Not yet specified

- Final information architecture, canvas interaction design, and accessibility details after the end-to-end UX prototype establishes the core interaction.
- Exact persisted schemas and migration policy after the Course state and workspace persistence prototypes settle their seams.
- Reviewer-agent and quantitative quality-evaluation design after the first vertical slice defines observable quality failures.
- Notebook, executable exercise, H5P/Moodle, mini-book, and NotebookLM export scope after the presentation-centered proof of concept is specified.
- Hosted collaboration, authentication, and multi-user behavior beyond a safe single-user remote mode.
- Product name, license, repository publication, and release branding.
- Google Drive and NotebookLM integration details beyond ensuring the Resource connector boundary can support them later.

## Out of scope

- Multi-tenancy, organizations, RBAC, and production SaaS operations.
- Round-trip synchronization from edited exported PowerPoint files into Course state.
- A promise that every malformed or non-structural PowerPoint template works without mapping or correction.
- Building an academic report as a separate deliverable.

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

- [Prove the chosen stack on Python 3.14](tickets/WF-001-prove-stack-compatibility-on-python-3-14.md) — The pinned Python, agent, API, frontend, PowerPoint, lint, and type-check stack interoperates and passes the repository checks.
- [Prototype the portable Course Workspace](tickets/WF-002-prototype-the-portable-course-workspace.md) — Keep canonical state readable and atomic, Git-initialize the Course, and store credentials and disposable runtime data elsewhere.
- [Sharpen the multi-lecture authoring model](tickets/WF-003-sharpen-the-course-authoring-model.md) — Use a chat-first, progressively authored flat Course Plan with stable identities, flexible shared Sources, autonomous validated working-state changes, recoverable partial Current State, and conservative Release validation.
- [Design the Course Agent runtime](tickets/WF-004-design-the-course-agent-runtime.md) — One persistent Course Agent owns mutations; bounded model-powered Worker Agents are deferred until after the first demo and are distinct from generic background jobs.
- [Design Resource ingestion and connectors](tickets/WF-005-design-resource-ingestion-and-connectors.md) — Use the provider-neutral Candidate-to-Citation lifecycle with immutable Snapshots, disposable representations, and explicit Course admission.
- [Evaluate grounding and retrieval](tickets/WF-006-evaluate-grounding-and-retrieval.md) — FTS5, bounded reads, and resolvable Evidence are sufficient for the product demo; no quantitative retrieval study or embeddings are required.
- [Prototype Template Profile onboarding](tickets/WF-007-prototype-template-profile-onboarding.md) — Combine deterministic inspection and heuristics with correction, calibration, optional consented model assistance, and structural export validation.
- [Decide Slide Preview fidelity](tickets/WF-008-decide-slide-preview-fidelity.md) — Use immediate template-backed browser overlays, optional authoritative native rendering, and a semantic fallback.
- [Prototype the end-to-end Course Author experience](tickets/WF-009-prototype-the-end-to-end-professor-experience.md) — Use a restricted launcher and a chat-first workspace with focused Syllabus, Library, Presentation, Model, and Template surfaces.
- [Design local distribution and provider configuration](tickets/WF-010-design-local-distribution-and-provider-configuration.md) — Package the bundled local app for eventual PyPI publication through `uvx` and `uv tool install`; direct Git installation is a convenience, Docker is deferred from the first demo, and no hosted service is planned.
- [Identify the demonstration](tickets/WF-012-identify-the-data-science-demonstration.md) — Deliver a polished working product demonstration, not a quantitative evaluation study or separate academic report.
- [Record validation constraints](tickets/WF-013-collect-professor-validation-assets.md) — Verify with public provenance-recorded fixtures, deterministic providers, optional free live checks, and LibreOffice on the development machine.
- [Specify the vertical slice](tickets/WF-014-specify-the-proof-of-concept-vertical-slice.md) — The durable PRD and local implementation queue define the proof-of-concept scope; the local ticket 16 release candidate is verified, while public distribution is deferred.

## Not yet specified

- A formal local security threat model, tracked by the open threat-model ticket. Optional container deployment was not retained for the first demo.
- Public repository and PyPI publication, deferred until the Course Author authorizes delivery and credentials are available.
- Threaded conversation history and reviewable context compaction, tracked separately from the proof-of-concept critical path.
- Notebook, executable exercise, H5P/Moodle, mini-book, and NotebookLM export scope after the presentation-centered proof of concept.
- Google Drive and NotebookLM integration details beyond ensuring the Resource connector boundary can support them later.

## Out of scope

- Multi-tenancy, organizations, RBAC, and production SaaS operations.
- A hosted Course Harness service or stable-format backward compatibility.
- Round-trip synchronization from edited exported PowerPoint files into Course state.
- A promise that every malformed or non-structural PowerPoint template works without mapping or correction.
- Building an academic report as a separate deliverable.
